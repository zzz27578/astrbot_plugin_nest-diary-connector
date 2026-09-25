from __future__ import annotations

import io
import json
import mimetypes
import shutil
import urllib.request
import zipfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.requests import Request

from .auth import verify_bearer_token_from_store
from .backup_service import BackupService
from .config import load_settings
from .diary.diary_service import DiaryService
from .memory.impression_service import ImpressionService
from .media.media_service import MediaService
from .memos.memo_service import MemoService
from .models import DiaryEntry, MemoEntry, PersonImpression, ServiceUiSettings
from .module_registry import (
    MODULE_ROUTE_PREFIX,
    OFFICIAL_APPEARANCE_IDS,
    OFFICIAL_MODULE_IDS,
    RESERVED_MODULE_DIR_NAMES,
    RUNTIME_PYTHON,
    STORE_MAX_KEYS,
    collect_nav_entries,
    is_official_id,
    module_asset_base,
    normalize_capabilities,
    resolve_asset,
    verify_page_entry,
)
from .module_store import ModuleStore, ModuleStoreError
from .paths import NestPaths, safe_package_id
from .settings_service import SecuritySettingsStore, ServiceSettingsStore
from .version_service import VersionService
from .web.routes import create_web_router, mount_static
from .web_auth import WebSessionAuth

APP_VERSION = "0.6.0"
settings = load_settings()
app = FastAPI(title="Nest Service", version=APP_VERSION)
WEB_DIST_DIR = Path(__file__).resolve().parent / "web_dist"
BUILTIN_APPEARANCE_ROOT = WEB_DIST_DIR / "appearance"
AVATAR_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
paths = NestPaths(settings.data_dir)
diary_service = DiaryService(paths)
media_service = MediaService(paths)
impression_service = ImpressionService(paths)
memo_service = MemoService(paths)
service_settings = ServiceSettingsStore(paths)
module_store = ModuleStore(paths)
backup_service = BackupService(paths)
version_service = VersionService(
    current_version=APP_VERSION,
    repo_root=Path(__file__).resolve().parents[1],
    enable_self_update=settings.enable_self_update,
)
security_settings = SecuritySettingsStore(
    paths,
    default_admin_password=settings.admin_password or "12345678",
    default_bot_api_token=settings.bot_api_token,
)
initial_security = security_settings.load()
web_auth = WebSessionAuth(
    admin_password=initial_security.admin_password,
    session_secret=initial_security.bot_api_token or "development-session-secret",
)
if (WEB_DIST_DIR / "assets").exists():
    app.mount("/app-assets", StaticFiles(directory=str(WEB_DIST_DIR / "assets")), name="app-assets")
mount_static(app)


def require_web_session(request: Request) -> None:
    if not web_auth.verify_session(request.cookies.get("nest_session")):
        raise HTTPException(status_code=401, detail="Web session required")


def request_future_task_sync() -> None:
    callback = getattr(app.state, "nest_future_task_sync", None)
    if callback:
        callback()


def spa_index(request: Request):
    if not web_auth.verify_session(request.cookies.get("nest_session")):
        return RedirectResponse("/login", status_code=303)
    index_path = WEB_DIST_DIR / "index.html"
    if not index_path.exists():
        return RedirectResponse("/diary", status_code=303)
    return FileResponse(index_path)


def spa_index_date(date: str, request: Request):
    return spa_index(request)


def spa_module_page(module_id: str, request: Request):
    """自定义模块页面统一走 /m/<module-id>，由 SPA 动态加载模块自带的 page.js。"""
    return spa_index(request)


for spa_route in [
    "/",
    "/dashboard",
    "/diary",
    "/write",
    "/search",
    "/impressions",
    "/media",
    "/memos",
    "/settings",
]:
    app.add_api_route(spa_route, spa_index, methods=["GET"], include_in_schema=False)
app.add_api_route("/diary/{date}", spa_index_date, methods=["GET"], include_in_schema=False)
app.add_api_route(f"{MODULE_ROUTE_PREFIX}/{{module_id}}", spa_module_page, methods=["GET"], include_in_schema=False)

app.include_router(
    create_web_router(
        web_auth,
        diary_service,
        media_service,
        diary_service.revisions,
        impression_service,
        service_settings,
        security_settings,
        web_auth,
        version_service,
        backup_service,
        settings,
    )
)


def _entry_payload(entry: DiaryEntry) -> dict:
    return {
        "id": f"{entry.notebook_id}:{entry.date}",
        "date": entry.date,
        "notebook_id": entry.notebook_id,
        "notebook_name": entry.notebook_name,
        "origin_umo": entry.origin_umo,
        "platform_id": entry.platform_id,
        "message_type": entry.message_type,
        "session_id": entry.session_id,
        "title": entry.normalized_title(),
        "mood": entry.mood,
        "tags": entry.tags,
        "people": entry.people,
        "media_refs": entry.media_refs,
        "importance": entry.importance,
        "source": entry.source,
        "revision": entry.revision,
        "body": entry.body,
    }


def _memo_payload(entry: MemoEntry, *, reveal_sensitive: bool = True) -> dict:
    content = entry.content
    if entry.sensitive and not reveal_sensitive:
        content = ""
    return {
        "id": entry.id,
        "title": entry.title,
        "content": content,
        "content_hidden": bool(entry.sensitive and not reveal_sensitive),
        "tags": entry.tags,
        "source_chat": entry.source_chat,
        "origin_umo": entry.origin_umo,
        "platform_id": entry.platform_id,
        "message_type": entry.message_type,
        "session_id": entry.session_id,
        "recorder": entry.recorder,
        "source": entry.source,
        "sensitive": entry.sensitive,
        "pinned": entry.pinned,
        "archived": entry.archived,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
        "deleted_at": entry.deleted_at,
    }


def _settings_payload() -> dict:
    return asdict(service_settings.load())


def _security_payload() -> dict:
    security = security_settings.load()
    return {
        "bot_api_token": security.bot_api_token,
        "external_api_enabled": security.external_api_enabled,
        "admin_password_set": bool(security.admin_password),
        "admin_password_is_default": security.admin_password == security_settings.default_admin_password,
    }


def _custom_webui_root(ui_settings: ServiceUiSettings | None = None) -> Path:
    loaded = ui_settings or service_settings.load()
    configured = loaded.custom_webui_dir.strip()
    if configured:
        return Path(configured).expanduser()
    return paths.user_custom_dir / "webui"


def _frontend_styles(ui_settings: ServiceUiSettings) -> list[dict]:
    styles = [{"id": "default", "name": "官方默认", "kind": "official", "description": "小窝内置基础样式。"}]
    seen = {"default"}
    for item in _discover_appearance_modules(ui_settings):
        if item.get("appearance_mode") != "global" or item["id"] in seen:
            continue
        styles.append(
            {
                "id": item["id"],
                "name": item["name"],
                "kind": item.get("kind", "appearance"),
                "description": item.get("description", ""),
            }
        )
        seen.add(item["id"])
    themes_dir = _custom_webui_root(ui_settings) / "themes"
    if themes_dir.exists():
        for path in sorted(themes_dir.iterdir()):
            if path.is_dir() and path.name not in seen:
                styles.append({"id": path.name, "name": path.name, "kind": "custom", "description": "自定义主题。"})
                seen.add(path.name)
    if ui_settings.active_frontend_style not in {item["id"] for item in styles}:
        styles.append({"id": ui_settings.active_frontend_style, "name": ui_settings.active_frontend_style, "kind": "missing", "description": "当前配置指向的样式不存在。"})
    return styles


def _module_catalog(ui_settings: ServiceUiSettings) -> dict:
    official = _discover_official_modules()
    custom = _discover_custom_packages(ui_settings, "modules", "module")
    extensions = _discover_custom_packages(ui_settings, "extensions", "extension")
    appearance = _discover_appearance_modules(ui_settings)
    return {
        "official": official,
        "custom": custom,
        "extensions": extensions,
        "appearance": appearance,
        "conflicts": _module_conflicts(ui_settings, official, custom, extensions),
        "appearance_conflicts": _appearance_conflicts(ui_settings, appearance),
        "nav_entries": _module_nav_entries(ui_settings, custom, extensions),
    }


def _module_nav_entries(ui_settings: ServiceUiSettings, custom: list[dict], extensions: list[dict]) -> list[dict]:
    """算出侧边栏应该多出哪些自定义入口。"""
    return collect_nav_entries(
        [
            (custom, ui_settings.enabled_custom_modules),
            (extensions, ui_settings.enabled_custom_extensions),
        ],
        hidden_ids=ui_settings.hidden_module_nav_ids,
    )


def _discover_official_modules() -> list[dict]:
    modules_root = Path(__file__).resolve().parents[1] / "modules"
    modules: list[dict] = []
    for module_id in OFFICIAL_MODULE_IDS:
        item = _load_package_manifest(
            modules_root / module_id / "module.json",
            {
                "id": module_id,
                "name": module_id,
                "type": "module",
                "description": "",
                "feature_tags": [f"{module_id}-core"],
                "replaces": [],
                "conflicts_with": [],
                "ui_category": "appearance" if module_id == "webui" else "core",
            },
            kind="official",
        )
        item["ui_category"] = item.get("ui_category") or ("appearance" if module_id == "webui" else "core")
        modules.append(item)
    return modules


def _contains_module_files(path: Path) -> bool:
    """判断目录是否包含 data/ 之外的模块文件。"""
    try:
        return any(child.name != "data" for child in path.iterdir())
    except OSError:
        return False


def _discover_custom_packages(ui_settings: ServiceUiSettings, folder_name: str, package_type: str) -> list[dict]:
    packages: dict[str, dict] = {}
    if package_type == "module":
        module_paths = sorted(paths.modules_dir.iterdir()) if paths.modules_dir.exists() else []
        for path in module_paths:
            if path.is_dir() and path.name not in RESERVED_MODULE_DIR_NAMES and _contains_module_files(path):
                packages[path.name] = _load_package_manifest(
                    path / "module.json",
                    {
                        "id": path.name,
                        "name": path.name,
                        "type": "module",
                        "description": "",
                        "feature_tags": [],
                        "replaces": [],
                        "conflicts_with": [],
                    },
                    kind="custom",
                    data_path=str(path),
                )
    else:
        extension_root = paths.modules_dir / "extensions"
        if extension_root.exists():
            for path in sorted(extension_root.iterdir()):
                if path.is_dir() and _contains_module_files(path):
                    packages[path.name] = _load_package_manifest(
                        path / "module.json",
                        {
                            "id": path.name,
                            "name": path.name,
                            "type": "extension",
                            "description": "",
                            "feature_tags": [],
                            "target_modules": [],
                            "conflicts_with": [],
                        },
                        kind="extension",
                        data_path=str(path),
                    )

    frontend_root = _custom_webui_root(ui_settings) / folder_name
    if frontend_root.exists():
        for path in sorted(frontend_root.iterdir()):
            if not path.is_dir():
                continue
            current = packages.get(path.name)
            loaded = _load_package_manifest(
                path / "module.json",
                {
                    "id": path.name,
                    "name": path.name,
                    "type": package_type,
                    "description": "",
                    "feature_tags": [],
                    "conflicts_with": [],
                },
                kind="extension" if package_type == "extension" else "custom",
                frontend_path=str(path),
            )
            if current:
                current["frontend_path"] = str(path)
                for key in ["name", "description", "feature_tags", "target_modules", "replaces", "conflicts_with"]:
                    if loaded.get(key):
                        current[key] = loaded[key]
                # 前端目录里的声明优先：页面文件就在那里，能力声明必须跟着它重算。
                for key in ["runtime", "nav", "page", "store"]:
                    if key in loaded:
                        current[key] = loaded[key]
                current.pop("capability_errors", None)
                current.pop("manifest_error", None)
                _attach_capabilities(current)
            else:
                packages[path.name] = loaded
    return sorted(packages.values(), key=lambda item: item["id"])


def _discover_appearance_modules(ui_settings: ServiceUiSettings) -> list[dict]:
    packages: dict[str, dict] = {}
    if BUILTIN_APPEARANCE_ROOT.exists():
        for path in sorted(BUILTIN_APPEARANCE_ROOT.iterdir()):
            if not path.is_dir():
                continue
            if not (path / "module.json").exists():
                continue
            loaded = _load_package_manifest(
                path / "module.json",
                {
                    "id": path.name,
                    "name": path.name,
                    "type": "appearance",
                    "description": "官方前端外观模块。",
                    "feature_tags": ["webui-appearance", "official-global-appearance"],
                    "appearance_scope": "global",
                    "appearance_mode": "global",
                    "conflicts_with": [],
                },
                kind="official",
                frontend_path=str(path),
            )
            loaded["appearance_scope"] = str(loaded.get("appearance_scope") or "global")
            loaded["appearance_mode"] = str(loaded.get("appearance_mode") or "global")
            loaded["entry_label"] = "全局样式" if loaded["appearance_mode"] == "global" else "外观拓展"
            packages[loaded["id"]] = loaded
    frontend_root = _custom_webui_root(ui_settings)
    for folder_name, default_mode in [("themes", "global"), ("appearance", "global"), ("skins", "global")]:
        root = frontend_root / folder_name
        if not root.exists():
            continue
        for path in sorted(root.iterdir()):
            if not path.is_dir():
                continue
            loaded = _load_package_manifest(
                path / "module.json",
                {
                    "id": path.name,
                    "name": path.name,
                    "type": "appearance",
                    "description": "替换小窝全局前端外观。",
                    "feature_tags": ["webui-appearance"],
                    "appearance_scope": default_mode,
                    "appearance_mode": "global",
                    "conflicts_with": [],
                },
                kind="appearance",
                frontend_path=str(path),
            )
            loaded["appearance_scope"] = str(loaded.get("appearance_scope") or default_mode)
            loaded["appearance_mode"] = str(loaded.get("appearance_mode") or "global")
            loaded["entry_label"] = "全局模块" if loaded["appearance_mode"] == "global" else "补充拓展"
            packages[path.name] = loaded
    for item in packages.values():
        item["appearance_scope"] = str(item.get("appearance_scope") or "global")
        item["appearance_mode"] = str(item.get("appearance_mode") or "global")
        item["entry_label"] = "全局模块" if item["appearance_mode"] == "global" else "补充拓展"
    return sorted(packages.values(), key=lambda item: item["id"])


def _load_package_manifest(path: Path, fallback: dict, kind: str, data_path: str = "", frontend_path: str = "") -> dict:
    data = dict(fallback)
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            data.update(loaded)
        except Exception:
            data["manifest_error"] = "module.json 读取失败"
    data["id"] = str(data.get("id") or path.parent.name)
    data["name"] = str(data.get("name") or data["id"])
    data["type"] = str(data.get("type") or fallback.get("type") or "module")
    data["kind"] = kind
    data["description"] = str(data.get("description") or "")
    for key in ["feature_tags", "target_modules", "replaces", "conflicts_with", "tools", "web_routes", "data_roots"]:
        if not isinstance(data.get(key), list):
            data[key] = []
    if data_path:
        data["data_path"] = data_path
    if frontend_path:
        data["frontend_path"] = frontend_path
    _attach_capabilities(data)
    return data


def _attach_capabilities(manifest: dict) -> dict:
    """解析并校验模块能力声明。

    三层把关的第二和第三层在这里：开发者声明经过归一化（第二层是用户开关，
    在设置里），框架再验真声明的页面文件是否真的存在（第三层）。
    校验不通过只降级该模块的入口，不影响小窝其余部分。
    """
    capabilities = normalize_capabilities(manifest)
    errors = list(capabilities.pop("errors", []))
    manifest["capabilities"] = capabilities
    manifest["asset_base"] = module_asset_base(manifest["id"])

    page_error = verify_page_entry(manifest)
    if page_error:
        errors.append(page_error)
        capabilities["page"] = None
        capabilities["nav"] = None

    nav = capabilities.get("nav")
    if nav and nav.get("icon_asset"):
        if resolve_asset(manifest, nav["icon_asset"]) is None:
            errors.append(f"nav 图标文件不存在：{nav['icon_asset']}")
            nav["icon_asset"] = ""
            nav["icon"] = "modules"

    if capabilities.get("runtime") == RUNTIME_PYTHON:
        # Python 档等于在插件进程里执行第三方代码，所以不允许安装即自动启用。
        manifest["requires_explicit_enable"] = True

    if errors:
        manifest["capability_errors"] = errors
        if not manifest.get("manifest_error"):
            manifest["manifest_error"] = errors[0]
    return manifest


def _timestamp_label() -> str:
    # 提高时间戳精度，避免连续卸载时备份目录重名
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _find_enabled_module(ui_settings: ServiceUiSettings, module_id: str) -> dict | None:
    """按用户开关找到一个已启用的自定义模块/拓展/外观包。

    这是三层把关的第二层：模块声明了能力，也必须被用户启用，
    资源与存储接口才会对它开放。
    """
    catalog = _module_catalog(ui_settings)
    groups = [
        (catalog.get("custom", []), ui_settings.enabled_custom_modules),
        (catalog.get("extensions", []), ui_settings.enabled_custom_extensions),
        (catalog.get("appearance", []), ui_settings.enabled_appearance_modules),
    ]
    for items, enabled_ids in groups:
        for item in items:
            if item.get("id") != module_id:
                continue
            if module_id in enabled_ids or ui_settings.active_frontend_style == module_id:
                return item
            return None
    return None


def _require_store_module(module_id: str) -> dict:
    safe_module = safe_package_id(module_id, "")
    if not safe_module or safe_module != module_id:
        raise HTTPException(status_code=400, detail="模块 ID 不合法。")
    ui_settings = service_settings.load()
    manifest = _find_enabled_module(ui_settings, safe_module)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"模块 {module_id} 未启用或不存在。")
    if not (manifest.get("capabilities", {}) or {}).get("store"):
        raise HTTPException(status_code=403, detail=f"模块 {module_id} 没有声明 store 能力。")
    return manifest


def _module_install_candidates(source_url: str) -> list[str]:
    source_url = source_url.strip()
    parsed = urlparse(source_url)
    if parsed.netloc.lower() != "github.com":
        return [source_url]
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return [source_url]
    owner, repo = parts[0], parts[1].removesuffix(".git")
    if len(parts) >= 4 and parts[2] in {"tree", "commits"}:
        branch = "/".join(parts[3:])
        return [f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{branch}"]
    if source_url.endswith(".zip"):
        return [source_url]
    return [
        f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/main",
        f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/master",
    ]


def _download_module_zip(source_url: str, max_bytes: int = 24 * 1024 * 1024) -> bytes:
    errors: list[str] = []
    for candidate in _module_install_candidates(source_url):
        try:
            request = urllib.request.Request(candidate, headers={"User-Agent": f"Nest/{APP_VERSION}"})
            with urllib.request.urlopen(request, timeout=20) as response:
                content_type = response.headers.get("content-type", "")
                payload = bytearray()
                while True:
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    payload.extend(chunk)
                    if len(payload) > max_bytes:
                        raise ValueError("模块包超过 24MB，请精简后再安装。")
                data = bytes(payload)
                if not data.startswith(b"PK") and "zip" not in content_type.lower():
                    raise ValueError("链接没有返回 zip 模块包。")
                return data
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")
    raise HTTPException(status_code=400, detail="模块下载失败：" + "；".join(errors[-2:]))


def _safe_zip_parts(name: str) -> tuple[str, ...] | None:
    # pathlib would honor "/x" and "C:/x" as absolute, so split zip names manually and reject anchors.
    parts = tuple(name.replace("\\", "/").split("/"))
    if not parts or any(part in {"", ".", ".."} or ":" in part for part in parts):
        return None
    return parts


def _manifest_from_zip(payload: bytes) -> tuple[dict, tuple[str, ...]]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        manifest_names = [
            item.filename
            for item in archive.infolist()
            if not item.is_dir() and item.filename.replace("\\", "/").endswith("module.json")
        ]
        if not manifest_names:
            raise HTTPException(status_code=400, detail="模块包缺少 module.json。")
        manifest_names.sort(key=lambda value: (value.count("/"), len(value)))
        manifest_name = manifest_names[0]
        try:
            manifest = json.loads(archive.read(manifest_name).decode("utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"module.json 解析失败：{exc}") from exc
        root_parts = Path(manifest_name).parts[:-1]
        return manifest, root_parts


def _module_install_target(manifest: dict, ui_settings: ServiceUiSettings) -> tuple[str, Path]:
    module_type = str(manifest.get("type") or "module").strip().lower()
    module_id = safe_package_id(str(manifest.get("id") or manifest.get("name") or "custom-module"), "custom-module")
    installed_appearance_ids = {item["id"] for item in _discover_appearance_modules(ui_settings) if item.get("kind") == "official"}
    if is_official_id(module_id) or module_id in installed_appearance_ids:
        raise HTTPException(status_code=409, detail=f"{module_id} 是官方模块 ID，不能通过链接安装覆盖。")
    if module_type == "extension":
        return module_id, paths.modules_dir / "extensions" / module_id
    if module_type == "appearance":
        return module_id, _custom_webui_root(ui_settings) / "appearance" / module_id
    return module_id, paths.modules_dir / module_id


MODULE_MAX_UNPACKED_BYTES = 64 * 1024 * 1024


def _extract_module_zip(payload: bytes, target: Path, root_parts: tuple[str, ...], overwrite: bool) -> dict:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        planned: list[tuple[zipfile.ZipInfo, tuple[str, ...]]] = []
        unpacked_bytes = 0
        for member in archive.infolist():
            if member.is_dir():
                continue
            parts = _safe_zip_parts(member.filename)
            if not parts:
                raise HTTPException(status_code=400, detail=f"模块包包含危险路径：{member.filename}")
            if root_parts:
                if parts[: len(root_parts)] != root_parts:
                    continue
                relative = parts[len(root_parts) :]
            else:
                relative = parts
            if not relative:
                raise HTTPException(status_code=400, detail=f"模块包包含危险路径：{member.filename}")
            unpacked_bytes += member.file_size
            planned.append((member, relative))
        if unpacked_bytes > MODULE_MAX_UNPACKED_BYTES:
            raise HTTPException(status_code=400, detail="模块包解压后超过 64MB，请精简后再安装。")

        backup_dir = ""
        if target.exists():
            if not overwrite:
                raise HTTPException(status_code=409, detail=f"模块 {target.name} 已存在。请先备份后覆盖，或换一个模块 ID。")
            backup_root = paths.root / "imports" / "module-install-backups" / _timestamp_label()
            backup_target = backup_root / target.name
            backup_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(target, backup_target)
            shutil.rmtree(target)
            backup_dir = str(backup_target)
        target.mkdir(parents=True, exist_ok=True)
        target_root = target.resolve()
        for member, relative in planned:
            destination = target.joinpath(*relative)
            if not destination.resolve().is_relative_to(target_root):
                raise HTTPException(status_code=400, detail=f"模块包包含危险路径：{member.filename}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(member))
    return {"installed_files": len(planned), "backup_dir": backup_dir}


def _appearance_conflicts(ui_settings: ServiceUiSettings, appearance: list[dict]) -> list[dict]:
    enabled = [item for item in appearance if item["id"] in ui_settings.enabled_appearance_modules]
    global_enabled = [item for item in enabled if item.get("appearance_mode") == "global"]
    if ui_settings.active_frontend_style != "default" and ui_settings.active_frontend_style not in ui_settings.enabled_appearance_modules:
        active_style = next((item for item in appearance if item["id"] == ui_settings.active_frontend_style), None)
        global_enabled.append(
            active_style
            or {
                "id": ui_settings.active_frontend_style,
                "name": f"当前样式 {ui_settings.active_frontend_style}",
                "appearance_mode": "global",
            }
        )
    if len(global_enabled) <= 1:
        return []
    return [
        {
            "level": "danger",
            "title": "全局外观冲突",
            "message": "、".join(item["name"] for item in global_enabled)
            + " 都会替换小窝全局前端。建议只开启其中一个；若坚持同时启用，样式、入口或脚本可能互相覆盖。",
            "packages": [item["id"] for item in global_enabled],
        }
    ]


def _module_conflicts(ui_settings: ServiceUiSettings, official: list[dict], custom: list[dict], extensions: list[dict]) -> list[dict]:
    enabled_official = [item for item in official if item["id"] in ui_settings.enabled_official_modules]
    enabled_custom = [item for item in custom if item["id"] in ui_settings.enabled_custom_modules]
    enabled_extensions = [item for item in extensions if item["id"] in ui_settings.enabled_custom_extensions]
    enabled: list[dict] = [*enabled_official, *enabled_custom, *enabled_extensions]

    warnings: list[dict] = []
    by_id = {item["id"]: item for item in enabled}
    by_tag: dict[str, list[str]] = {}
    for item in enabled:
        if item not in enabled_extensions:
            for tag in item.get("feature_tags", []):
                by_tag.setdefault(tag, []).append(item["id"])
        for replaced in item.get("replaces", []):
            if replaced in by_id:
                warnings.append(
                    {
                        "level": "warning",
                        "title": "替代关系",
                        "message": f"{item['name']} 声明替代 {by_id[replaced]['name']}，建议只启用其中一个。",
                        "packages": [item["id"], replaced],
                    }
                )
        for target in item.get("conflicts_with", []):
            if target in by_id:
                warnings.append(
                    {
                        "level": "danger",
                        "title": "显式冲突",
                        "message": f"{item['name']} 与 {by_id[target]['name']} 声明冲突，建议禁用一个。",
                        "packages": [item["id"], target],
                    }
                )
    for tag, package_ids in by_tag.items():
        if len(package_ids) > 1:
            names = "、".join(by_id[item_id]["name"] for item_id in package_ids)
            warnings.append(
                {
                    "level": "warning",
                    "title": "功能标签重叠",
                    "message": f"{names} 都提供 `{tag}`。可以同时启用，但可能出现入口重复或数据口径不一致。",
                    "packages": package_ids,
                }
            )
    replacement_modules = [item for item in enabled_custom if item.get("replaces") or item.get("conflicts_with")]
    if len(replacement_modules) > 1:
        warnings.append(
            {
                "level": "danger",
                "title": "完整替换模块过多",
                "message": "、".join(item["name"] for item in replacement_modules)
                + " 都声明会替换或冲突于现有能力。建议一次只启用一个完整替换模块；若坚持同时启用，可能出现入口、数据口径或工具行为冲突。",
                "packages": [item["id"] for item in replacement_modules],
            }
        )
    return warnings


class DiaryWriteRequest(BaseModel):
    date: str
    body: str
    title: str | None = None
    notebook_id: str = "default"
    notebook_name: str = ""
    origin_umo: str = ""
    platform_id: str = ""
    message_type: str = ""
    session_id: str = ""
    mood: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    people: list[str] = Field(default_factory=list)
    media_refs: list[str] = Field(default_factory=list)
    importance: int = 3
    source: str = "bot"
    reason: str = ""
    intent: str = "write_diary"
    idempotency_key: str | None = None


def require_bot_token(authorization: str | None = Header(default=None)) -> None:
    verify_bearer_token_from_store(
        lambda: ((loaded := security_settings.load()).bot_api_token, loaded.external_api_enabled),
        authorization,
    )


def require_diary_module_enabled() -> None:
    if not service_settings.load().enable_diary_module:
        raise HTTPException(status_code=403, detail="Diary module is disabled")


def require_impressions_module_enabled() -> None:
    if not service_settings.load().enable_impressions_module:
        raise HTTPException(status_code=403, detail="Impressions module is disabled")


def require_media_module_enabled() -> None:
    if not service_settings.load().enable_media_module:
        raise HTTPException(status_code=403, detail="Media module is disabled")


def require_memos_module_enabled() -> None:
    if not service_settings.load().enable_memos_module:
        raise HTTPException(status_code=403, detail="Memos module is disabled")


def _touch_impressions_from_diary(entry: DiaryEntry) -> list[PersonImpression]:
    ui_settings = service_settings.load()
    if not ui_settings.enable_impressions_module or not ui_settings.auto_impression_from_diary:
        return []
    if ui_settings.impression_write_level == "off" or ui_settings.impression_update_strategy == "manual":
        return []
    return impression_service.touch_from_diary(
        entry,
        allow_new_people=ui_settings.impression_allow_new_people
        or ui_settings.impression_update_strategy == "aggressive",
        update_existing=ui_settings.impression_update_strategy in {"evidence_only", "existing_only", "aggressive"},
        min_confidence=ui_settings.impression_min_confidence,
    )


@app.get("/api/v1/status")
async def status(_auth: None = Depends(require_bot_token)):
    return {
        "status": "ok",
        "service": "nest",
        "version": APP_VERSION,
        "data_dir": str(settings.data_dir),
        "framework_dir": str(paths.framework_dir),
        "modules_dir": str(paths.modules_dir),
    }


@app.post("/api/v1/diary/write")
async def write_diary(
    payload: DiaryWriteRequest,
    _auth: None = Depends(require_bot_token),
    _module: None = Depends(require_diary_module_enabled),
):
    ui_settings = service_settings.load()
    media_refs = payload.media_refs if ui_settings.enable_media_module and ui_settings.allow_media_refs else []
    entry = DiaryEntry(
        date=payload.date,
        notebook_id=payload.notebook_id,
        notebook_name=payload.notebook_name,
        origin_umo=payload.origin_umo,
        platform_id=payload.platform_id,
        message_type=payload.message_type,
        session_id=payload.session_id,
        title=payload.title,
        body=payload.body,
        mood=payload.mood,
        tags=payload.tags,
        people=payload.people,
        media_refs=media_refs,
        importance=payload.importance,
        source=payload.source,
    )
    saved = diary_service.write_diary(entry, reason=payload.reason)
    touched = _touch_impressions_from_diary(saved)
    return {
        "status": "ok",
        "date": saved.date,
        "notebook_id": saved.notebook_id,
        "notebook_name": saved.notebook_name,
        "title": saved.normalized_title(),
        "impressions_touched": [item.name for item in touched],
    }


@app.get("/api/v1/diary/search")
async def search_diary(
    q: str,
    top_k: int = 8,
    snippet_chars: int = 180,
    notebook_id: str = "",
    _auth: None = Depends(require_bot_token),
    _module: None = Depends(require_diary_module_enabled),
):
    return {
        "query": q,
        "results": diary_service.search(q, top_k=top_k, snippet_chars=snippet_chars, notebook_id=notebook_id or None),
        "search": diary_service.search_status(),
    }


@app.get("/api/v1/diary/archive")
async def diary_archive(
    notebook_id: str = "",
    _auth: None = Depends(require_bot_token),
    _module: None = Depends(require_diary_module_enabled),
):
    return {"items": diary_service.archive_tree(notebook_id=notebook_id or None)}


@app.get("/api/v1/diary/notebooks")
async def diary_notebooks(
    _auth: None = Depends(require_bot_token),
    _module: None = Depends(require_diary_module_enabled),
):
    return {"items": diary_service.list_notebooks()}


@app.get("/api/v1/diary/{date}")
async def read_diary(
    date: str,
    notebook_id: str = "default",
    _auth: None = Depends(require_bot_token),
    _module: None = Depends(require_diary_module_enabled),
):
    try:
        entry = diary_service.read_by_date(date, notebook_id=notebook_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Diary entry not found") from None
    return {
        "date": entry.date,
        "notebook_id": entry.notebook_id,
        "notebook_name": entry.notebook_name,
        "origin_umo": entry.origin_umo,
        "platform_id": entry.platform_id,
        "message_type": entry.message_type,
        "session_id": entry.session_id,
        "title": entry.normalized_title(),
        "mood": entry.mood,
        "tags": entry.tags,
        "people": entry.people,
        "media_refs": entry.media_refs,
        "importance": entry.importance,
        "source": entry.source,
        "revision": entry.revision,
        "body": entry.body,
    }


class MediaAttachRequest(BaseModel):
    source_path: str
    date: str
    original_name: str | None = None
    note: str = ""
    actor_is_admin: bool = False
    autonomous: bool = True


class MediaResolveRequest(BaseModel):
    media_ref: str = ""
    date: str = ""
    original_name: str = ""


class MediaFolderCreateRequest(BaseModel):
    name: str = ""
    tags: list[str] = Field(default_factory=list)
    note: str = ""


class MediaFolderUpdateRequest(BaseModel):
    folder_id: str
    name: str = ""
    tags: list[str] = Field(default_factory=list)
    note: str = ""


class MediaMoveRequest(BaseModel):
    sha256: str
    folder_id: str = ""


class MediaTrashRequest(BaseModel):
    item_type: str
    item_id: str


class MediaNoteUpdateRequest(BaseModel):
    sha256: str
    note: str = ""


class MemoWriteRequest(BaseModel):
    title: str = ""
    content: str
    tags: list[str] = Field(default_factory=list)
    source_chat: str = ""
    origin_umo: str = ""
    platform_id: str = ""
    message_type: str = ""
    session_id: str = ""
    recorder: str = "human"
    source: str = "manual"
    sensitive: bool = False
    pinned: bool = False
    archived: bool = False
    actor_is_admin: bool = False
    autonomous: bool = False


class MemoUpdateRequest(BaseModel):
    id: str
    title: str | None = None
    content: str | None = None
    tags: list[str] | None = None
    source_chat: str | None = None
    sensitive: bool | None = None
    pinned: bool | None = None
    archived: bool | None = None


@app.post("/api/v1/media/attach")
async def attach_media(
    payload: MediaAttachRequest,
    _auth: None = Depends(require_bot_token),
    _module: None = Depends(require_media_module_enabled),
):
    ui_settings = service_settings.load()
    if ui_settings.media_auto_save_policy == "admin_only" and not payload.actor_is_admin:
        raise HTTPException(status_code=403, detail="Only the small-nest administrator can save media")
    if ui_settings.media_auto_save_policy == "admin_allowed" and not payload.actor_is_admin:
        raise HTTPException(status_code=403, detail="Only the small-nest administrator can save media")
    if payload.autonomous and not ui_settings.media_allow_bot_import:
        raise HTTPException(status_code=403, detail="Bot media import is disabled")
    if ui_settings.media_auto_save_limit_12h and media_service.count_saved_since(12) >= ui_settings.media_auto_save_limit_12h:
        raise HTTPException(status_code=400, detail="Media 12-hour limit reached")
    if len(media_service.list_by_date(payload.date).get("assets", [])) >= ui_settings.media_max_items_per_day:
        raise HTTPException(status_code=400, detail="Media limit reached for this date")
    source = Path(payload.source_path)
    if not source.exists():
        raise HTTPException(status_code=404, detail="Media source file not found")
    record = media_service.save_media(
        source,
        date=payload.date,
        original_name=payload.original_name,
        note=payload.note,
        storage_strategy=ui_settings.media_storage_strategy,
    )
    return {"status": "ok", "asset": record}


@app.post("/api/v1/media/resolve")
async def resolve_media(
    payload: MediaResolveRequest,
    _auth: None = Depends(require_bot_token),
    _module: None = Depends(require_media_module_enabled),
):
    asset = media_service.find_asset(
        media_ref=payload.media_ref,
        date=payload.date,
        original_name=payload.original_name,
    )
    if not asset:
        raise HTTPException(status_code=404, detail="Media asset not found")
    return {"status": "ok", "asset": asset}


@app.get("/api/v1/media/by-date/{date}")
async def list_media_by_date(
    date: str,
    _auth: None = Depends(require_bot_token),
    _module: None = Depends(require_media_module_enabled),
):
    return media_service.list_by_date(date)


@app.get("/media/blobs/{digest}")
async def read_media_blob(digest: str):
    blob = media_service.find_blob(digest)
    if not blob:
        raise HTTPException(status_code=404, detail="Media blob not found")
    media_type = mimetypes.guess_type(blob.name)[0] or "application/octet-stream"
    return FileResponse(blob, media_type=media_type)


class ImpressionWriteRequest(BaseModel):
    previous_name: str = ""
    name: str
    summary: str
    qq_id: str = ""
    source_chat: str = ""
    group_impressions: list[dict] = Field(default_factory=list)
    identity: str = ""
    traits: list[str] = Field(default_factory=list)
    hobbies: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    relationship: str = ""
    affinity: int = 3
    special_comment: str = ""
    evidence_dates: list[str] = Field(default_factory=list)
    confidence: int = 3
    notes: str = ""


class SettingsUpdateRequest(BaseModel):
    site_title: str = "小窝"
    site_subtitle: str = "把今天安放好，旧事也能被轻轻找回来"
    brand_avatar_url: str = ""
    search_default_top_k: int = 5
    search_snippet_chars: int = 180
    memory_recall_enabled: bool = True
    memory_recall_policy: str = "conservative"
    enable_diary_module: bool = True
    diary_archive_granularity: str = "day"
    diary_display_mode: str = "grouped"
    admin_private_diary_enabled: bool = False
    admin_private_push_enabled: bool = False
    diary_push_format: str = "text"
    diary_push_target: str = "none"
    diary_t2i_template_name: str = "plain_note"
    diary_image_send_max_retries: int = 3
    diary_image_send_failure_notice: bool = True
    permissions_allow_admin_natural_language: bool = True
    non_admin_permissions: list[str] = Field(default_factory=list)
    nest_admin_ids: str = ""
    diary_write_prompt: str = ""
    diary_t2i_template: str = ""
    enable_media_module: bool = True
    allow_media_refs: bool = True
    media_max_items_per_day: int = 80
    media_auto_save_policy: str = "admin_only"
    media_auto_save_limit_12h: int = 10
    media_auto_album_strategy: str = "confirm"
    media_allow_bot_import: bool = True
    media_auto_album: bool = True
    media_storage_strategy: str = "copy"
    enable_impressions_module: bool = True
    auto_impression_from_diary: bool = False
    impression_write_level: str = "balanced"
    impression_update_strategy: str = "evidence_only"
    impression_identity_strategy: str = "separate"
    impression_allow_new_people: bool = False
    impression_min_confidence: int = 3
    show_impression_prompt: bool = True
    enable_memos_module: bool = True
    memos_write_policy: str = "admin_only"
    memos_auto_write_limit_12h: int = 12
    memos_sensitive_default_hidden: bool = True
    active_frontend_style: str = "default"
    enabled_official_modules: list[str] = Field(default_factory=lambda: ["diary", "impressions", "media", "memos", "webui"])
    enabled_custom_modules: list[str] = Field(default_factory=list)
    enabled_custom_extensions: list[str] = Field(default_factory=list)
    enabled_appearance_modules: list[str] = Field(default_factory=list)
    hidden_module_nav_ids: list[str] = Field(default_factory=list)
    appearance_modules_initialized: bool = True
    onboarding_completed: bool = False
    custom_webui_dir: str = ""
    backup_custom_before_update: bool = True
    impression_prompt: str = ""


class SecurityUpdateRequest(BaseModel):
    admin_password: str | None = None
    bot_api_token: str = ""
    generate_bot_api_token: bool = False
    external_api_enabled: bool = False


class NotebookUpdateRequest(BaseModel):
    notebooks: list[dict] = Field(default_factory=list)
    delete_ids: list[str] = Field(default_factory=list)
    replace: bool = False


class OnboardingUpdateRequest(BaseModel):
    completed: bool = True


class ModuleInstallRequest(BaseModel):
    source_url: str
    overwrite: bool = False
    enable_after_install: bool = False


class ModuleUninstallRequest(BaseModel):
    module_id: str
    keep_data: bool = False


class ModuleStoreWriteRequest(BaseModel):
    key: str
    value: object = None


@app.get("/api/v1/impressions")
async def list_impressions(
    _auth: None = Depends(require_bot_token),
    _module: None = Depends(require_impressions_module_enabled),
):
    return {"items": [item.__dict__ for item in impression_service.list_people()]}


@app.get("/api/v1/impressions/{name}")
async def read_impression(
    name: str,
    _auth: None = Depends(require_bot_token),
    _module: None = Depends(require_impressions_module_enabled),
):
    impression = impression_service.get(name)
    if not impression:
        raise HTTPException(status_code=404, detail="Person impression not found")
    return impression.__dict__


@app.post("/api/v1/impressions/write")
async def write_impression(
    payload: ImpressionWriteRequest,
    _auth: None = Depends(require_bot_token),
    _module: None = Depends(require_impressions_module_enabled),
):
    previous_name = payload.previous_name.strip()
    next_name = payload.name.strip()
    if not next_name:
        raise HTTPException(status_code=400, detail="Person name is required")
    if previous_name and previous_name != next_name:
        impression_service.delete(previous_name)
    ui_settings = service_settings.load()
    saved = impression_service.save(
        PersonImpression(
            name=next_name,
            summary=payload.summary.strip(),
            qq_id=payload.qq_id.strip(),
            group_impressions=payload.group_impressions,
            identity=payload.identity.strip(),
            traits=payload.traits,
            hobbies=payload.hobbies,
            interests=payload.interests,
            preferences=payload.preferences,
            relationship=payload.relationship.strip(),
            affinity=payload.affinity,
            special_comment=payload.special_comment.strip(),
            evidence_dates=payload.evidence_dates,
            confidence=payload.confidence,
            notes=payload.notes.strip(),
        ),
        identity_strategy=ui_settings.impression_identity_strategy,
        source_chat=payload.source_chat,
        merge_existing=True,
    )
    return {"status": "ok", "item": saved.__dict__}


@app.delete("/api/v1/impressions/{name}")
async def delete_impression(
    name: str,
    _auth: None = Depends(require_bot_token),
    _module: None = Depends(require_impressions_module_enabled),
):
    if not impression_service.delete(name):
        raise HTTPException(status_code=404, detail="Person impression not found")
    return {"status": "ok"}


@app.get("/api/v1/memos")
async def list_memos(
    q: str = "",
    include_archived: bool = False,
    _auth: None = Depends(require_bot_token),
    _module: None = Depends(require_memos_module_enabled),
):
    return {
        "items": [
            _memo_payload(item, reveal_sensitive=True)
            for item in memo_service.list_memos(query=q, include_archived=include_archived)
        ],
        "summary": memo_service.summary(),
    }


@app.get("/api/v1/memos/{memo_id}")
async def read_memo(
    memo_id: str,
    _auth: None = Depends(require_bot_token),
    _module: None = Depends(require_memos_module_enabled),
):
    memo = memo_service.get(memo_id)
    if not memo:
        raise HTTPException(status_code=404, detail="Memo not found")
    return _memo_payload(memo, reveal_sensitive=True)


@app.post("/api/v1/memos/write")
async def write_memo(
    payload: MemoWriteRequest,
    _auth: None = Depends(require_bot_token),
    _module: None = Depends(require_memos_module_enabled),
):
    ui_settings = service_settings.load()
    policy = ui_settings.memos_write_policy
    if policy in {"admin_only", "admin_allowed"} and not payload.actor_is_admin:
        raise HTTPException(status_code=403, detail="Only the small-nest administrator can save memos")
    if payload.autonomous and policy not in {"bot_curated", "review"} and not payload.actor_is_admin:
        raise HTTPException(status_code=403, detail="Bot memo writing is disabled")
    if payload.autonomous and ui_settings.memos_auto_write_limit_12h:
        if memo_service.count_saved_since(12, recorder="bot") >= ui_settings.memos_auto_write_limit_12h:
            raise HTTPException(status_code=400, detail="Memo 12-hour limit reached")
    try:
        saved = memo_service.create(
            title=payload.title,
            content=payload.content,
            tags=[*payload.tags, *(["待复核"] if payload.autonomous and policy == "review" else [])],
            source_chat=payload.source_chat,
            origin_umo=payload.origin_umo,
            platform_id=payload.platform_id,
            message_type=payload.message_type,
            session_id=payload.session_id,
            recorder=payload.recorder or ("bot" if payload.autonomous else "human"),
            source=payload.source or ("bot_autonomous" if payload.autonomous else "manual"),
            sensitive=payload.sensitive,
            pinned=payload.pinned,
            archived=payload.archived,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "item": _memo_payload(saved, reveal_sensitive=True)}


@app.delete("/api/v1/memos/{memo_id}")
async def delete_memo(
    memo_id: str,
    _auth: None = Depends(require_bot_token),
    _module: None = Depends(require_memos_module_enabled),
):
    if not memo_service.delete(memo_id):
        raise HTTPException(status_code=404, detail="Memo not found")
    return {"status": "ok"}


@app.get("/api/ui/bootstrap")
async def ui_bootstrap(_session: None = Depends(require_web_session)):
    ui_settings = service_settings.load()
    entries = diary_service.list_entries()
    media = media_service.list_manifests()
    people = impression_service.list_people()
    memos = memo_service.summary()
    return {
        "version": APP_VERSION,
        "service": "nest",
        "stats": {
            "entries": len(entries),
            "media": sum(len(item.get("assets", [])) for item in media),
            "people": len(people),
            "memos": memos.get("count", 0),
        },
        "recent_entries": [_entry_payload(entry) for entry in entries[:6]],
        "archive": diary_service.archive_tree(),
        "settings": _settings_payload(),
        "security": _security_payload(),
        "notebooks": diary_service.list_notebooks(),
        "search": diary_service.search_status(),
        "frontend_styles": _frontend_styles(ui_settings),
        "module_catalog": _module_catalog(ui_settings),
        "data_dir": str(settings.data_dir),
        "framework_dir": str(paths.framework_dir),
        "modules_dir": str(paths.modules_dir),
    }


@app.get("/api/ui/diary")
async def ui_list_diary(notebook_id: str = "", _session: None = Depends(require_web_session)):
    return {
        "items": [_entry_payload(entry) for entry in diary_service.list_entries(notebook_id=notebook_id or None)],
        "archive": diary_service.archive_tree(notebook_id=notebook_id or None),
        "notebooks": diary_service.list_notebooks(),
    }


@app.get("/api/ui/diary/{date}")
async def ui_read_diary(date: str, notebook_id: str = "default", _session: None = Depends(require_web_session)):
    try:
        return _entry_payload(diary_service.read_by_date(date, notebook_id=notebook_id))
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Diary entry not found") from None


@app.post("/api/ui/diary")
async def ui_write_diary(payload: DiaryWriteRequest, _session: None = Depends(require_web_session)):
    ui_settings = service_settings.load()
    if not ui_settings.enable_diary_module:
        raise HTTPException(status_code=403, detail="Diary module is disabled")
    media_refs = payload.media_refs if ui_settings.enable_media_module and ui_settings.allow_media_refs else []
    entry = DiaryEntry(
        date=payload.date,
        notebook_id=payload.notebook_id,
        notebook_name=payload.notebook_name,
        origin_umo=payload.origin_umo,
        platform_id=payload.platform_id,
        message_type=payload.message_type,
        session_id=payload.session_id,
        title=payload.title,
        body=payload.body,
        mood=payload.mood,
        tags=payload.tags,
        people=payload.people,
        media_refs=media_refs,
        importance=payload.importance,
        source=payload.source or "admin",
    )
    saved = diary_service.write_diary(entry, reason=payload.reason or "web_app_update")
    touched = _touch_impressions_from_diary(saved)
    return {"status": "ok", "entry": _entry_payload(saved), "impressions_touched": [item.name for item in touched]}


@app.delete("/api/ui/diary/{date}")
async def ui_delete_diary(date: str, notebook_id: str = "default", _session: None = Depends(require_web_session)):
    deleted = diary_service.delete_diary(date, reason="web_app_delete", notebook_id=notebook_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Diary entry not found")
    return {"status": "ok"}


@app.get("/api/ui/search")
async def ui_search(q: str = "", top_k: int = 5, notebook_id: str = "", _session: None = Depends(require_web_session)):
    ui_settings = service_settings.load()
    if not q or not ui_settings.enable_diary_module:
        return {"query": q, "results": [], "search": diary_service.search_status(), "notebooks": diary_service.list_notebooks()}
    return {
        "query": q,
        "results": diary_service.search(q, top_k=top_k or ui_settings.search_default_top_k, snippet_chars=ui_settings.search_snippet_chars, notebook_id=notebook_id or None),
        "search": diary_service.search_status(),
        "notebooks": diary_service.list_notebooks(),
    }


@app.get("/api/ui/notebooks")
async def ui_list_notebooks(_session: None = Depends(require_web_session)):
    return {"items": diary_service.list_notebooks()}


@app.post("/api/ui/notebooks")
async def ui_save_notebooks(payload: NotebookUpdateRequest, _session: None = Depends(require_web_session)):
    saved = diary_service.save_notebooks(
        payload.notebooks,
        delete_ids=payload.delete_ids,
        replace=payload.replace,
    )
    request_future_task_sync()
    return {
        "status": "ok",
        "items": saved,
    }


@app.get("/api/ui/impressions")
async def ui_list_impressions(_session: None = Depends(require_web_session)):
    if not service_settings.load().enable_impressions_module:
        return {"items": []}
    return {"items": [item.__dict__ for item in impression_service.list_people()]}


@app.get("/api/ui/impressions/{name}")
async def ui_read_impression(name: str, _session: None = Depends(require_web_session)):
    if not service_settings.load().enable_impressions_module:
        raise HTTPException(status_code=403, detail="Impressions module is disabled")
    impression = impression_service.get(name)
    if not impression:
        raise HTTPException(status_code=404, detail="Person impression not found")
    return impression.__dict__


@app.post("/api/ui/impressions")
async def ui_write_impression(payload: ImpressionWriteRequest, _session: None = Depends(require_web_session)):
    if not service_settings.load().enable_impressions_module:
        raise HTTPException(status_code=403, detail="Impressions module is disabled")
    previous_name = payload.previous_name.strip()
    next_name = payload.name.strip()
    if not next_name:
        raise HTTPException(status_code=400, detail="Person name is required")
    if previous_name and previous_name != next_name:
        impression_service.delete(previous_name)
    ui_settings = service_settings.load()
    saved = impression_service.save(
        PersonImpression(
            name=next_name,
            summary=payload.summary.strip(),
            qq_id=payload.qq_id.strip(),
            group_impressions=payload.group_impressions,
            identity=payload.identity.strip(),
            traits=payload.traits,
            hobbies=payload.hobbies,
            interests=payload.interests,
            preferences=payload.preferences,
            relationship=payload.relationship.strip(),
            affinity=payload.affinity,
            special_comment=payload.special_comment.strip(),
            evidence_dates=payload.evidence_dates,
            confidence=payload.confidence,
            notes=payload.notes.strip(),
        ),
        identity_strategy=ui_settings.impression_identity_strategy,
        source_chat=payload.source_chat,
    )
    return {"status": "ok", "item": saved.__dict__}


@app.delete("/api/ui/impressions/{name}")
async def ui_delete_impression(name: str, _session: None = Depends(require_web_session)):
    if not service_settings.load().enable_impressions_module:
        raise HTTPException(status_code=403, detail="Impressions module is disabled")
    if not impression_service.delete(name):
        raise HTTPException(status_code=404, detail="Person impression not found")
    return {"status": "ok"}


@app.get("/api/ui/media")
async def ui_media(_session: None = Depends(require_web_session)):
    if not service_settings.load().enable_media_module:
        return {"items": [], "storage": {"bytes": 0, "count": 0, "label": "0 B"}, "organization": media_service.load_organization()}
    return {
        "items": media_service.list_manifests(),
        "storage": media_service.storage_summary(),
        "organization": media_service.load_organization(),
    }


@app.post("/api/ui/media/folders")
async def ui_create_media_folder(payload: MediaFolderCreateRequest, _session: None = Depends(require_web_session)):
    if not service_settings.load().enable_media_module:
        raise HTTPException(status_code=403, detail="Media module is disabled")
    folder = media_service.create_folder(payload.name, tags=payload.tags, note=payload.note)
    return {"status": "ok", "folder": folder, "organization": media_service.load_organization()}


@app.post("/api/ui/media/folders/update")
async def ui_update_media_folder(payload: MediaFolderUpdateRequest, _session: None = Depends(require_web_session)):
    if not service_settings.load().enable_media_module:
        raise HTTPException(status_code=403, detail="Media module is disabled")
    try:
        folder = media_service.update_folder(payload.folder_id, payload.name, tags=payload.tags, note=payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "folder": folder, "organization": media_service.load_organization()}


@app.post("/api/ui/media/move")
async def ui_move_media(payload: MediaMoveRequest, _session: None = Depends(require_web_session)):
    if not service_settings.load().enable_media_module:
        raise HTTPException(status_code=403, detail="Media module is disabled")
    try:
        organization = media_service.move_asset_to_folder(payload.sha256, payload.folder_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "organization": organization}


@app.post("/api/ui/media/note")
async def ui_update_media_note(payload: MediaNoteUpdateRequest, _session: None = Depends(require_web_session)):
    if not service_settings.load().enable_media_module:
        raise HTTPException(status_code=403, detail="Media module is disabled")
    try:
        asset = media_service.update_asset_note(payload.sha256, payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "asset": asset}


@app.post("/api/ui/media/trash")
async def ui_trash_media(payload: MediaTrashRequest, _session: None = Depends(require_web_session)):
    if not service_settings.load().enable_media_module:
        raise HTTPException(status_code=403, detail="Media module is disabled")
    try:
        organization = media_service.trash_item(payload.item_type, payload.item_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "organization": organization}


@app.post("/api/ui/media/restore")
async def ui_restore_media(payload: MediaTrashRequest, _session: None = Depends(require_web_session)):
    if not service_settings.load().enable_media_module:
        raise HTTPException(status_code=403, detail="Media module is disabled")
    organization = media_service.restore_item(payload.item_type, payload.item_id)
    return {"status": "ok", "organization": organization}


@app.post("/api/ui/media/delete")
async def ui_delete_media(payload: MediaTrashRequest, _session: None = Depends(require_web_session)):
    if not service_settings.load().enable_media_module:
        raise HTTPException(status_code=403, detail="Media module is disabled")
    try:
        organization = media_service.delete_item(payload.item_type, payload.item_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "organization": organization}


@app.get("/api/ui/memos")
async def ui_list_memos(
    q: str = "",
    include_archived: bool = False,
    reveal_sensitive: bool = False,
    _session: None = Depends(require_web_session),
):
    ui_settings = service_settings.load()
    if not ui_settings.enable_memos_module:
        return {"items": [], "summary": memo_service.summary()}
    reveal = reveal_sensitive or not ui_settings.memos_sensitive_default_hidden
    return {
        "items": [
            _memo_payload(item, reveal_sensitive=reveal)
            for item in memo_service.list_memos(query=q, include_archived=include_archived)
        ],
        "summary": memo_service.summary(),
    }


@app.post("/api/ui/memos")
async def ui_write_memo(payload: MemoWriteRequest, _session: None = Depends(require_web_session)):
    if not service_settings.load().enable_memos_module:
        raise HTTPException(status_code=403, detail="Memos module is disabled")
    try:
        saved = memo_service.create(
            title=payload.title,
            content=payload.content,
            tags=payload.tags,
            source_chat=payload.source_chat,
            origin_umo=payload.origin_umo,
            platform_id=payload.platform_id,
            message_type=payload.message_type,
            session_id=payload.session_id,
            recorder=payload.recorder or "human",
            source=payload.source or "web",
            sensitive=payload.sensitive,
            pinned=payload.pinned,
            archived=payload.archived,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "item": _memo_payload(saved, reveal_sensitive=True), "summary": memo_service.summary()}


@app.post("/api/ui/memos/update")
async def ui_update_memo(payload: MemoUpdateRequest, _session: None = Depends(require_web_session)):
    if not service_settings.load().enable_memos_module:
        raise HTTPException(status_code=403, detail="Memos module is disabled")
    try:
        saved = memo_service.update(
            payload.id,
            title=payload.title,
            content=payload.content,
            tags=payload.tags,
            source_chat=payload.source_chat,
            sensitive=payload.sensitive,
            pinned=payload.pinned,
            archived=payload.archived,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "item": _memo_payload(saved, reveal_sensitive=True), "summary": memo_service.summary()}


@app.delete("/api/ui/memos/{memo_id}")
async def ui_delete_memo(memo_id: str, _session: None = Depends(require_web_session)):
    if not service_settings.load().enable_memos_module:
        raise HTTPException(status_code=403, detail="Memos module is disabled")
    if not memo_service.delete(memo_id):
        raise HTTPException(status_code=404, detail="Memo not found")
    return {"status": "ok", "summary": memo_service.summary()}


@app.get("/api/ui/health")
async def ui_data_health(_session: None = Depends(require_web_session)):
    return {"status": "ok", "health": backup_service.data_health_summary()}


@app.get("/api/ui/settings")
async def ui_get_settings(_session: None = Depends(require_web_session)):
    ui_settings = service_settings.load()
    return {
        "settings": _settings_payload(),
        "security": _security_payload(),
        "search": diary_service.search_status(),
        "notebooks": diary_service.list_notebooks(),
        "frontend_styles": _frontend_styles(ui_settings),
        "module_catalog": _module_catalog(ui_settings),
        "health": backup_service.data_health_summary(),
        "runtime": {
            "data_dir": str(settings.data_dir),
            "framework_dir": str(paths.framework_dir),
            "modules_dir": str(paths.modules_dir),
            "custom_webui_dir": str(_custom_webui_root(ui_settings)),
            "port": settings.port,
            "self_update": settings.enable_self_update,
        },
    }


@app.post("/api/ui/onboarding")
async def ui_save_onboarding(payload: OnboardingUpdateRequest, _session: None = Depends(require_web_session)):
    current = service_settings.load()
    current.onboarding_completed = bool(payload.completed)
    saved = service_settings.save(current)
    return {"status": "ok", "settings": asdict(saved)}


@app.post("/api/ui/modules/install")
async def ui_install_module(payload: ModuleInstallRequest, _session: None = Depends(require_web_session)):
    source_url = payload.source_url.strip()
    if not source_url:
        raise HTTPException(status_code=400, detail="请填写模块链接。")
    parsed = urlparse(source_url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="模块链接必须使用 http 或 https。")
    ui_settings = service_settings.load()
    zip_payload = _download_module_zip(source_url)
    manifest, root_parts = _manifest_from_zip(zip_payload)
    module_id, target = _module_install_target(manifest, ui_settings)
    result = _extract_module_zip(zip_payload, target, root_parts, overwrite=payload.overwrite)
    installed_manifest = _load_package_manifest(
        target / "module.json",
        {
            "id": module_id,
            "name": module_id,
            "type": str(manifest.get("type") or "module"),
            "description": "",
            "feature_tags": [],
            "conflicts_with": [],
        },
        kind=(
            "appearance"
            if str(manifest.get("type") or "module").lower() == "appearance"
            else "extension"
            if str(manifest.get("type") or "module").lower() == "extension"
            else "custom"
        ),
        data_path=str(target) if str(manifest.get("type") or "module").lower() != "appearance" else "",
        frontend_path=str(target) if str(manifest.get("type") or "module").lower() == "appearance" else "",
    )
    module_type = installed_manifest.get("type", "module")
    requires_explicit_enable = bool(installed_manifest.get("requires_explicit_enable"))
    auto_enable = bool(payload.enable_after_install) and not requires_explicit_enable
    if auto_enable:
        if module_type == "appearance":
            ui_settings.enabled_appearance_modules = list(dict.fromkeys([*ui_settings.enabled_appearance_modules, module_id]))
            if installed_manifest.get("appearance_mode", "global") == "global":
                ui_settings.active_frontend_style = module_id
        elif module_type == "extension":
            ui_settings.enabled_custom_extensions = list(dict.fromkeys([*ui_settings.enabled_custom_extensions, module_id]))
        else:
            ui_settings.enabled_custom_modules = list(dict.fromkeys([*ui_settings.enabled_custom_modules, module_id]))
        service_settings.save(ui_settings)
    fresh_settings = service_settings.load()
    return {
        "status": "ok",
        "module": installed_manifest,
        "install": result,
        "enabled": auto_enable,
        "requires_explicit_enable": requires_explicit_enable,
        "capabilities": installed_manifest.get("capabilities", {}),
        "capability_errors": installed_manifest.get("capability_errors", []),
        "module_catalog": _module_catalog(fresh_settings),
        "frontend_styles": _frontend_styles(fresh_settings),
    }


@app.post("/api/ui/modules/uninstall")
async def ui_uninstall_module(payload: ModuleUninstallRequest, _session: None = Depends(require_web_session)):
    """卸载自定义模块、扩展包或外观包。官方 ID 一律拒绝。

    默认先完整备份所有相关目录再删除；keep_data 为真时仅保留
    modules/<id>/data 或 modules/extensions/<id>/data。
    """
    module_id = safe_package_id(payload.module_id.strip(), "")
    if not module_id or module_id != payload.module_id.strip():
        raise HTTPException(status_code=400, detail="模块 ID 不合法")
    if is_official_id(module_id):
        raise HTTPException(status_code=409, detail=f"{module_id} 是官方模块，不允许卸载")

    ui_settings = service_settings.load()
    custom_root = _custom_webui_root(ui_settings)
    locations = [
        ("module", paths.modules_dir / module_id, True),
        ("extension", paths.modules_dir / "extensions" / module_id, True),
        ("frontend-module", custom_root / "modules" / module_id, False),
        ("frontend-extension", custom_root / "extensions" / module_id, False),
        ("appearance", custom_root / "appearance" / module_id, False),
        ("theme", custom_root / "themes" / module_id, False),
        ("skin", custom_root / "skins" / module_id, False),
    ]
    existing: list[tuple[str, Path, bool]] = []
    seen_paths: set[Path] = set()
    for label, path, can_keep_data in locations:
        resolved = path.resolve()
        if resolved in seen_paths or not path.is_dir():
            continue
        seen_paths.add(resolved)
        existing.append((label, path, can_keep_data))
    if not existing:
        raise HTTPException(status_code=404, detail=f"未找到模块 {module_id}")

    backup_root = paths.root / "imports" / "module-uninstall-backups" / _timestamp_label() / module_id
    # 先完整备份所有相关目录，避免备份失败时只删除了部分模块文件
    for label, path, _can_keep_data in existing:
        target = backup_root / label
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(path, target)

    removed: list[str] = []
    kept: list[str] = []
    for _label, path, can_keep_data in existing:
        data_dir = path / "data"
        if payload.keep_data and can_keep_data and data_dir.is_dir():
            for child in list(path.iterdir()):
                if child == data_dir:
                    continue
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            kept.append(str(data_dir))
            removed.append(str(path))
            continue
        shutil.rmtree(path)
        removed.append(str(path))

    ui_settings.enabled_custom_modules = [item for item in ui_settings.enabled_custom_modules if item != module_id]
    ui_settings.enabled_custom_extensions = [item for item in ui_settings.enabled_custom_extensions if item != module_id]
    ui_settings.enabled_appearance_modules = [item for item in ui_settings.enabled_appearance_modules if item != module_id]
    ui_settings.hidden_module_nav_ids = [item for item in ui_settings.hidden_module_nav_ids if item != module_id]
    if ui_settings.active_frontend_style == module_id:
        ui_settings.active_frontend_style = "default"
    saved = service_settings.save(ui_settings)

    return {
        "status": "ok",
        "module_id": module_id,
        "removed_paths": removed,
        "kept_paths": kept,
        "backup_dir": str(backup_root),
        "module_catalog": _module_catalog(saved),
        "frontend_styles": _frontend_styles(saved),
        "settings": asdict(saved),
    }


@app.get("/api/ui/module-assets/{module_id}/{asset_path:path}")
async def ui_module_asset(module_id: str, asset_path: str, _session: None = Depends(require_web_session)):
    """按模块 ID 提供其自带的前端资源。

    路由收在 /api/ui/ 前缀下，是为了让 AstrBot 插件页的 bridge 白名单
    不必为第三方模块反复放开。
    """
    safe_module = safe_package_id(module_id, "")
    if not safe_module or safe_module != module_id:
        raise HTTPException(status_code=400, detail="模块 ID 不合法。")
    ui_settings = service_settings.load()
    manifest = _find_enabled_module(ui_settings, safe_module)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"模块 {module_id} 未启用或不存在。")
    resolved = resolve_asset(manifest, asset_path)
    if resolved is None:
        raise HTTPException(status_code=404, detail="资源不存在或类型不被允许。")
    media_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
    return FileResponse(resolved, media_type=media_type)


@app.get("/api/ui/modules/{module_id}/store")
async def ui_module_store_list(module_id: str, key: str = "", _session: None = Depends(require_web_session)):
    manifest = _require_store_module(module_id)
    try:
        if key:
            return {"status": "ok", "module_id": manifest["id"], **module_store.read(manifest["id"], key)}
        return {"status": "ok", "module_id": manifest["id"], "keys": module_store.list_keys(manifest["id"])}
    except ModuleStoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@app.post("/api/ui/modules/{module_id}/store")
async def ui_module_store_write(module_id: str, payload: ModuleStoreWriteRequest, _session: None = Depends(require_web_session)):
    manifest = _require_store_module(module_id)
    max_bytes = (manifest.get("capabilities", {}).get("store") or {}).get("max_bytes")
    try:
        result = module_store.write(manifest["id"], payload.key, payload.value, max_bytes=max_bytes)
    except ModuleStoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"status": "ok", "module_id": manifest["id"], **result}


@app.delete("/api/ui/modules/{module_id}/store")
async def ui_module_store_delete(module_id: str, key: str, _session: None = Depends(require_web_session)):
    manifest = _require_store_module(module_id)
    try:
        result = module_store.delete(manifest["id"], key)
    except ModuleStoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"status": "ok", "module_id": manifest["id"], **result}


@app.get("/api/ui/avatar")
async def ui_avatar(_session: None = Depends(require_web_session)):
    for suffix in AVATAR_EXTENSIONS:
        path = paths.framework_dir / "assets" / f"brand-avatar{suffix}"
        if path.exists():
            return FileResponse(path)
    raise HTTPException(status_code=404, detail="Avatar not found")


@app.post("/api/ui/avatar")
async def ui_upload_avatar(file: UploadFile = File(...), _session: None = Depends(require_web_session)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in AVATAR_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Only png, jpg, webp or gif avatars are supported")
    target_dir = paths.framework_dir / "assets"
    target_dir.mkdir(parents=True, exist_ok=True)
    for old_suffix in AVATAR_EXTENSIONS:
        old = target_dir / f"brand-avatar{old_suffix}"
        if old.exists():
            old.unlink()
    target = target_dir / f"brand-avatar{suffix}"
    content = await file.read()
    if len(content) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Avatar must be 2MB or smaller")
    target.write_bytes(content)
    avatar_url = f"/api/ui/avatar?v={int(target.stat().st_mtime)}"
    current = service_settings.load()
    current.brand_avatar_url = avatar_url
    service_settings.save(current)
    return {"status": "ok", "avatar_url": avatar_url}


@app.post("/api/ui/settings")
async def ui_save_settings(payload: SettingsUpdateRequest, _session: None = Depends(require_web_session)):
    saved = service_settings.save(
        ServiceUiSettings(
            site_title=payload.site_title,
            site_subtitle=payload.site_subtitle,
            brand_avatar_url=payload.brand_avatar_url,
            search_default_top_k=payload.search_default_top_k,
            search_snippet_chars=payload.search_snippet_chars,
            memory_recall_enabled=payload.memory_recall_enabled,
            memory_recall_policy=payload.memory_recall_policy,
            enable_diary_module=payload.enable_diary_module,
            diary_archive_granularity=payload.diary_archive_granularity,
            diary_display_mode=payload.diary_display_mode,
            admin_private_diary_enabled=payload.admin_private_diary_enabled,
            admin_private_push_enabled=payload.admin_private_push_enabled,
            diary_push_format=payload.diary_push_format,
            diary_push_target=payload.diary_push_target,
            diary_t2i_template_name=payload.diary_t2i_template_name,
            diary_image_send_max_retries=payload.diary_image_send_max_retries,
            diary_image_send_failure_notice=payload.diary_image_send_failure_notice,
            permissions_allow_admin_natural_language=payload.permissions_allow_admin_natural_language,
            non_admin_permissions=payload.non_admin_permissions,
            nest_admin_ids=payload.nest_admin_ids,
            diary_write_prompt=payload.diary_write_prompt,
            diary_t2i_template=payload.diary_t2i_template,
            enable_media_module=payload.enable_media_module,
            allow_media_refs=payload.allow_media_refs,
            media_max_items_per_day=payload.media_max_items_per_day,
            media_auto_save_policy=payload.media_auto_save_policy,
            media_auto_save_limit_12h=payload.media_auto_save_limit_12h,
            media_auto_album_strategy=payload.media_auto_album_strategy,
            media_allow_bot_import=payload.media_allow_bot_import,
            media_auto_album=payload.media_auto_album,
            media_storage_strategy=payload.media_storage_strategy,
            enable_impressions_module=payload.enable_impressions_module,
            auto_impression_from_diary=payload.auto_impression_from_diary,
            impression_write_level=payload.impression_write_level,
            impression_update_strategy=payload.impression_update_strategy,
            impression_identity_strategy=payload.impression_identity_strategy,
            impression_allow_new_people=payload.impression_allow_new_people,
            impression_min_confidence=payload.impression_min_confidence,
            show_impression_prompt=payload.show_impression_prompt,
            enable_memos_module=payload.enable_memos_module,
            memos_write_policy=payload.memos_write_policy,
            memos_auto_write_limit_12h=payload.memos_auto_write_limit_12h,
            memos_sensitive_default_hidden=payload.memos_sensitive_default_hidden,
            active_frontend_style=payload.active_frontend_style,
            enabled_official_modules=payload.enabled_official_modules,
            enabled_custom_modules=payload.enabled_custom_modules,
            enabled_custom_extensions=payload.enabled_custom_extensions,
            enabled_appearance_modules=payload.enabled_appearance_modules,
            hidden_module_nav_ids=payload.hidden_module_nav_ids,
            appearance_modules_initialized=True,
            onboarding_completed=payload.onboarding_completed,
            custom_webui_dir=payload.custom_webui_dir,
            backup_custom_before_update=payload.backup_custom_before_update,
            impression_prompt=payload.impression_prompt,
        )
    )
    request_future_task_sync()
    return {"status": "ok", "settings": asdict(saved)}


@app.post("/api/ui/security")
async def ui_save_security(payload: SecurityUpdateRequest, _session: None = Depends(require_web_session)):
    token = security_settings.generate_token() if payload.generate_bot_api_token else payload.bot_api_token
    saved = security_settings.update(
        admin_password=(payload.admin_password or "").strip() or None,
        bot_api_token=token.strip(),
        external_api_enabled=payload.external_api_enabled,
    )
    web_auth.admin_password = saved.admin_password
    web_auth.session_secret = saved.bot_api_token or "development-session-secret"
    return {"status": "ok", "security": _security_payload()}


@app.get("/api/ui/version/check")
async def ui_check_version(_session: None = Depends(require_web_session)):
    try:
        result = version_service.check_latest()
        return {
            "status": "ok",
            "current": result.current,
            "latest": result.latest,
            "update_available": result.update_available,
            "source": result.source,
            "message": "发现新版本。" if result.update_available else "当前已经是最新版本。",
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"版本检测失败：{exc}") from exc


@app.post("/api/ui/version/update")
async def ui_update_version(_session: None = Depends(require_web_session)):
    result = version_service.update()
    return {
        "status": "ok" if result.ok else "blocked",
        "ok": result.ok,
        "message": result.message,
        "output": result.output,
    }


@app.get("/api/ui/export")
async def ui_export_backup(
    package_type: str = "full",
    module_id: str = "",
    include_security: bool = False,
    _session: None = Depends(require_web_session),
):
    content = backup_service.export_zip(
        package_type=package_type,
        module_id=module_id,
        include_security=include_security,
        nest_version=APP_VERSION,
    )
    package_label = "selected" if "," in package_type else (package_type or "full")
    suffix = f"-{module_id}" if module_id else ""
    filename = f"nest-{package_label}{suffix}.zip"
    return Response(
        content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/ui/import/preview")
async def ui_preview_import_backup(
    backup_file: UploadFile = File(...),
    _session: None = Depends(require_web_session),
):
    payload = await backup_file.read()
    try:
        preview = backup_service.preview_zip(payload)
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Invalid zip backup file") from exc
    preview["current_health"] = backup_service.data_health_summary()
    return {"status": "ok", "preview": preview}


@app.post("/api/ui/import")
async def ui_import_backup(
    backup_file: UploadFile = File(...),
    strategy: str = Form("safe"),
    _session: None = Depends(require_web_session),
):
    payload = await backup_file.read()
    result = backup_service.import_zip(payload, strategy=strategy)
    protocol_audit = diary_service.notebooks.audit_protocols()
    indexed = diary_service.rebuild_index()
    result["protocol_audit"] = protocol_audit
    result["reindexed_diaries"] = indexed
    request_future_task_sync()
    return {"status": "ok", "result": result}
