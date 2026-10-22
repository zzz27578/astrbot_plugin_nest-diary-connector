from __future__ import annotations

import os
import json
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from nest_diary_web.models import DiaryEntry, PersonImpression, ServiceUiSettings
from nest_diary_web.web_auth import WebSessionAuth

WEB_DIR = Path(__file__).parent
DEFAULT_TEMPLATES_DIR = WEB_DIR / "templates"
DEFAULT_STATIC_DIR = WEB_DIR / "static"
BUILTIN_WEB_DIST_DIR = WEB_DIR.parent / "web_dist"
BUILTIN_APPEARANCE_ROOT = BUILTIN_WEB_DIST_DIR / "appearance"
CUSTOM_WEBUI_DIR = Path(os.environ["NEST_CUSTOM_WEBUI_DIR"]).expanduser() if os.environ.get("NEST_CUSTOM_WEBUI_DIR") else None

template_dirs: list[str] = []
if CUSTOM_WEBUI_DIR and (CUSTOM_WEBUI_DIR / "templates").exists():
    template_dirs.append(str(CUSTOM_WEBUI_DIR / "templates"))
template_dirs.append(str(DEFAULT_TEMPLATES_DIR))
templates = Jinja2Templates(directory=template_dirs)


def create_web_router(
    auth: WebSessionAuth,
    diary_service=None,
    media_service=None,
    revision_service=None,
    impression_service=None,
    settings_store=None,
    security_store=None,
    web_auth=None,
    version_service=None,
    backup_service=None,
    runtime_settings=None,
) -> APIRouter:
    router = APIRouter()

    def require_login(request: Request):
        return auth.redirect_if_missing(request.cookies.get("nest_session"))

    def load_ui_settings() -> ServiceUiSettings:
        return settings_store.load() if settings_store else ServiceUiSettings()

    def custom_webui_root(settings: ServiceUiSettings | None = None) -> Path:
        loaded = settings or load_ui_settings()
        configured = loaded.custom_webui_dir.strip()
        if configured:
            return Path(configured).expanduser()
        if CUSTOM_WEBUI_DIR:
            return CUSTOM_WEBUI_DIR
        if runtime_settings:
            return Path(runtime_settings.data_dir) / "framework" / "user_custom" / "webui"
        return Path("framework") / "user_custom" / "webui"

    def discover_frontend_styles(settings: ServiceUiSettings) -> list[dict]:
        themes_dir = custom_webui_root(settings) / "themes"
        styles = [{"id": "default", "name": "官方默认", "kind": "official"}]
        seen = {"default"}
        for item in discover_appearance_modules(settings):
            if item.get("appearance_mode") != "global" or item["id"] in seen:
                continue
            styles.append({"id": item["id"], "name": item["name"], "kind": item.get("kind", "appearance")})
            seen.add(item["id"])
        if themes_dir.exists():
            for path in sorted(themes_dir.iterdir()):
                if path.is_dir() and path.name not in seen:
                    styles.append({"id": path.name, "name": path.name, "kind": "custom"})
                    seen.add(path.name)
        if settings.active_frontend_style not in {item["id"] for item in styles}:
            styles.append({"id": settings.active_frontend_style, "name": settings.active_frontend_style, "kind": "missing"})
        return styles

    def discover_appearance_modules(settings: ServiceUiSettings) -> list[dict]:
        packages: dict[str, dict] = {}

        def load_manifest(path: Path, fallback: dict, kind: str, frontend_path: Path) -> dict:
            data = dict(fallback)
            manifest_path = path / "module.json"
            if manifest_path.exists():
                try:
                    data.update(json.loads(manifest_path.read_text(encoding="utf-8")))
                except Exception:
                    data["manifest_error"] = "module.json 读取失败"
            data["id"] = str(data.get("id") or path.name)
            data["name"] = str(data.get("name") or data["id"])
            data["description"] = str(data.get("description") or "")
            data["kind"] = kind
            data["frontend_path"] = str(frontend_path)
            if not isinstance(data.get("feature_tags"), list):
                data["feature_tags"] = []
            if not isinstance(data.get("conflicts_with"), list):
                data["conflicts_with"] = []
            data["appearance_mode"] = str(data.get("appearance_mode") or "global")
            data["appearance_scope"] = str(data.get("appearance_scope") or "global")
            data["entry_label"] = "全局模块" if data["appearance_mode"] == "global" else "补充拓展"
            return data

        if BUILTIN_APPEARANCE_ROOT.exists():
            for builtin_path in sorted(BUILTIN_APPEARANCE_ROOT.iterdir()):
                if not builtin_path.is_dir():
                    continue
                if not (builtin_path / "module.json").exists():
                    continue
                item = load_manifest(
                    builtin_path,
                    {
                        "id": builtin_path.name,
                        "name": builtin_path.name,
                        "type": "appearance",
                        "description": "官方前端外观模块。",
                        "feature_tags": ["webui-appearance", "official-global-appearance"],
                        "appearance_mode": "global",
                        "conflicts_with": [],
                    },
                    "official",
                    builtin_path,
                )
                packages[item["id"]] = item
        for folder_name in ["themes", "appearance", "skins"]:
            root = custom_webui_root(settings) / folder_name
            if not root.exists():
                continue
            for path in sorted(root.iterdir()):
                if path.is_dir():
                    packages[path.name] = load_manifest(
                        path,
                        {
                            "id": path.name,
                            "name": path.name,
                            "type": "appearance",
                            "description": "替换小窝全局前端外观。",
                            "feature_tags": ["webui-appearance"],
                            "appearance_mode": "global",
                            "conflicts_with": [],
                        },
                        "appearance",
                        path,
                    )
        return sorted(packages.values(), key=lambda item: item["id"])

    def discover_modules(settings: ServiceUiSettings) -> dict:
        official = [
            {"id": "diary", "name": "日记", "description": "写入、读取、归档和检索"},
            {"id": "impressions", "name": "人物印象", "description": "长期人物认识"},
            {"id": "media", "name": "媒体", "description": "图片、语音和附件归档"},
            {"id": "webui", "name": "WebUI", "description": "网页后台与设置页"},
        ]
        data_root = Path(runtime_settings.data_dir) if runtime_settings else Path("data")

        def load_manifest(path: Path, fallback: dict) -> dict:
            data = dict(fallback)
            manifest_path = path / "module.json"
            if manifest_path.exists():
                try:
                    data.update(json.loads(manifest_path.read_text(encoding="utf-8")))
                except Exception:
                    data["manifest_error"] = "module.json 读取失败"
            data["id"] = str(data.get("id") or path.name)
            data["name"] = str(data.get("name") or data["id"])
            data["description"] = str(data.get("description") or "")
            return data

        custom_modules = {}
        for root in [data_root / "modules", custom_webui_root(settings) / "modules"]:
            if not root.exists():
                continue
            for path in sorted(root.iterdir()):
                if path.is_dir() and path.name not in {"diary", "impressions", "media", "extensions", "archive"}:
                    item = load_manifest(path, {"id": path.name, "name": path.name, "path": str(path)})
                    item["path"] = str(path)
                    custom_modules[path.name] = item

        extensions = {}
        for root in [data_root / "modules" / "extensions", custom_webui_root(settings) / "extensions"]:
            if not root.exists():
                continue
            for path in sorted(root.iterdir()):
                if path.is_dir():
                    item = load_manifest(path, {"id": path.name, "name": path.name, "path": str(path), "type": "extension"})
                    item["path"] = str(path)
                    extensions[path.name] = item

        return {
            "official": official,
            "custom": sorted(custom_modules.values(), key=lambda item: item["id"]),
            "extensions": sorted(extensions.values(), key=lambda item: item["id"]),
            "conflicts": [],
        }

    @router.get("/theme.css")
    async def active_theme_css():
        settings = load_ui_settings()
        css_parts: list[str] = []
        for item in discover_appearance_modules(settings):
            if item["id"] not in settings.enabled_appearance_modules and item["id"] != settings.active_frontend_style:
                continue
            css_path = Path(item.get("frontend_path", "")) / "style.css"
            if css_path.exists():
                css_parts.append(f"/* {item['id']} */\n{css_path.read_text(encoding='utf-8')}")
        css_path = custom_webui_root(settings) / "themes" / settings.active_frontend_style / "style.css"
        if (
            settings.active_frontend_style != "default"
            and settings.active_frontend_style not in settings.enabled_appearance_modules
            and css_path.exists()
        ):
            css_parts.append(f"/* active_frontend_style:{settings.active_frontend_style} */\n{css_path.read_text(encoding='utf-8')}")
        return Response("\n\n".join(css_parts), media_type="text/css")

    @router.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        redirect = require_login(request)
        if redirect:
            return redirect
        entries = diary_service.list_entries() if diary_service else []
        media = media_service.list_manifests() if media_service else []
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "entries_count": len(entries),
                "media_count": sum(len(item.get("assets", [])) for item in media),
                "people_count": len(impression_service.list_people()) if impression_service else 0,
                "recent_entries": entries[:5],
                "active": "dashboard",
                "archive": diary_service.archive_tree() if diary_service else [],
            },
        )

    @router.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        return templates.TemplateResponse(request, "login.html", {"error": "", "default_password": "12345678"})

    @router.post("/login")
    async def login(request: Request, password: str = Form(...)):
        if not auth.verify_password(password):
            return templates.TemplateResponse(
                request,
                "login.html",
                {"error": "密码不正确", "default_password": "12345678"},
                status_code=401,
            )
        response = RedirectResponse("/", status_code=303)
        response.set_cookie("nest_session", auth.create_session_token(), httponly=True, samesite="lax")
        return response

    @router.post("/logout")
    async def logout():
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie("nest_session")
        return response

    @router.get("/write", response_class=HTMLResponse)
    async def write_page(request: Request, date: str = "", saved: str = "", error: str = ""):
        redirect = require_login(request)
        if redirect:
            return redirect
        ui_settings = load_ui_settings()
        if not ui_settings.enable_diary_module:
            return templates.TemplateResponse(
                request,
                "write.html",
                {
                    "selected_entry": None,
                    "saved": "",
                    "error": "日记模块当前已关闭。",
                    "active": "write",
                    "ui_settings": ui_settings,
                },
            )
        selected_entry = None
        if date and diary_service:
            try:
                selected_entry = diary_service.read_by_date(date)
            except FileNotFoundError:
                error = error or f"{date} 没有找到日记"
        return templates.TemplateResponse(
            request,
            "write.html",
            {
                "selected_entry": selected_entry,
                "saved": saved,
                "error": error,
                "active": "write",
                "ui_settings": ui_settings,
            },
        )

    @router.get("/search", response_class=HTMLResponse)
    async def search_page(request: Request, q: str = ""):
        redirect = require_login(request)
        if redirect:
            return redirect
        ui_settings = load_ui_settings()
        results = (
            diary_service.search(q, top_k=ui_settings.search_default_top_k, snippet_chars=ui_settings.search_snippet_chars)
            if q and diary_service and ui_settings.enable_diary_module
            else []
        )
        return templates.TemplateResponse(
            request,
            "search.html",
            {"q": q, "results": results, "active": "search", "archive": diary_service.archive_tree() if diary_service else []},
        )

    @router.get("/diary", response_class=HTMLResponse)
    async def diary_page(request: Request, date: str = "", saved: str = "", error: str = ""):
        redirect = require_login(request)
        if redirect:
            return redirect
        ui_settings = load_ui_settings()
        if not ui_settings.enable_diary_module:
            return templates.TemplateResponse(
                request,
                "diary.html",
                {
                    "entries": [],
                    "selected_entry": None,
                    "saved": saved,
                    "error": error or "日记模块当前已关闭。",
                    "active": "diary",
                    "archive": [],
                },
            )
        entries = diary_service.list_entries() if diary_service else []
        selected_entry = None
        if date and diary_service:
            try:
                selected_entry = diary_service.read_by_date(date)
            except FileNotFoundError:
                error = error or f"{date} 没有找到日记"
        elif entries:
            selected_entry = entries[0]
        return templates.TemplateResponse(
            request,
            "diary.html",
            {
                "entries": entries,
                "selected_entry": selected_entry,
                "saved": saved,
                "error": error,
                "active": "diary",
                "archive": diary_service.archive_tree() if diary_service else [],
            },
        )

    @router.get("/media", response_class=HTMLResponse)
    async def media_page(request: Request):
        redirect = require_login(request)
        if redirect:
            return redirect
        manifests = media_service.list_manifests() if media_service else []
        return templates.TemplateResponse(request, "media.html", {"manifests": manifests, "active": "media"})

    @router.get("/impressions", response_class=HTMLResponse)
    async def impressions_page(request: Request, name: str = "", saved: str = "", error: str = ""):
        redirect = require_login(request)
        if redirect:
            return redirect
        people = impression_service.list_people() if impression_service else []
        selected_person = impression_service.get(name) if name and impression_service else None
        if name and not selected_person:
            error = error or f"{name} 没有找到人物印象"
        return templates.TemplateResponse(
            request,
            "impressions.html",
            {"people": people, "selected_person": selected_person, "saved": saved, "error": error, "active": "impressions"},
        )

    @router.get("/settings", response_class=HTMLResponse)
    async def settings_page(
        request: Request,
        saved: str = "",
        error: str = "",
        version_message: str = "",
        version_current: str = "",
        version_latest: str = "",
        update_available: str = "",
    ):
        redirect = require_login(request)
        if redirect:
            return redirect
        settings = load_ui_settings()
        return templates.TemplateResponse(
            request,
            "settings.html",
            {
                "settings": settings,
                "security": security_store.load() if security_store else None,
                "runtime_settings": runtime_settings,
                "version_service": version_service,
                "search_status": diary_service.search_status() if diary_service else {},
                "frontend_styles": discover_frontend_styles(settings),
                "module_catalog": discover_modules(settings),
                "version_message": version_message,
                "version_current": version_current,
                "version_latest": version_latest,
                "update_available": update_available,
                "saved": saved,
                "error": error,
                "active": "settings",
            },
        )

    @router.get("/panel", response_class=HTMLResponse)
    async def panel_redirect():
        return RedirectResponse("/write", status_code=303)

    @router.post("/write/diary")
    async def save_diary(
        request: Request,
        date: str = Form(...),
        title: str = Form(""),
        body: str = Form(...),
        mood: str = Form(""),
        tags: str = Form(""),
        people: str = Form(""),
        media_refs: str = Form(""),
        importance: int = Form(3),
    ):
        redirect = require_login(request)
        if redirect:
            return redirect
        ui_settings = settings_store.load() if settings_store else ServiceUiSettings()
        if not ui_settings.enable_diary_module:
            return RedirectResponse("/write?error=diary-module-disabled", status_code=303)
        if not diary_service:
            return RedirectResponse("/write?error=diary-service-unavailable", status_code=303)
        entry = DiaryEntry(
            date=date.strip(),
            title=title.strip() or None,
            body=body.strip(),
            mood=_split_words(mood),
            tags=_split_words(tags),
            people=_split_words(people),
            media_refs=_split_lines(media_refs),
            importance=max(1, min(int(importance), 5)),
            source="admin",
        )
        diary_service.write_diary(entry, reason="web_admin_update", mode="replace")
        return RedirectResponse(f"/write?date={entry.date}&saved=1", status_code=303)

    @router.post("/diary/delete")
    async def delete_diary(request: Request, date: str = Form(...)):
        redirect = require_login(request)
        if redirect:
            return redirect
        if not diary_service:
            return RedirectResponse("/diary?error=diary-service-unavailable", status_code=303)
        deleted = diary_service.delete_diary(date.strip(), reason="web_admin_delete")
        if not deleted:
            return RedirectResponse(f"/diary?error={quote(date + ' 没有找到日记')}", status_code=303)
        return RedirectResponse("/diary?saved=1", status_code=303)

    @router.post("/panel/diary")
    async def save_diary_legacy(request: Request):
        return RedirectResponse("/write", status_code=303)

    @router.post("/impressions")
    async def save_impression(
        request: Request,
        name: str = Form(...),
        summary: str = Form(...),
        identity: str = Form(""),
        traits: str = Form(""),
        hobbies: str = Form(""),
        interests: str = Form(""),
        preferences: str = Form(""),
        relationship: str = Form(""),
        affinity: int = Form(3),
        special_comment: str = Form(""),
        evidence_dates: str = Form(""),
        confidence: int = Form(3),
        notes: str = Form(""),
    ):
        redirect = require_login(request)
        if redirect:
            return redirect
        if not impression_service:
            return RedirectResponse("/impressions?error=impression-service-unavailable", status_code=303)
        item = PersonImpression(
            name=name.strip(),
            summary=summary.strip(),
            identity=identity.strip(),
            traits=_split_words(traits),
            hobbies=_split_words(hobbies),
            interests=_split_words(interests),
            preferences=_split_words(preferences),
            relationship=relationship.strip(),
            affinity=max(1, min(int(affinity), 5)),
            special_comment=special_comment.strip(),
            evidence_dates=_split_words(evidence_dates),
            confidence=max(1, min(int(confidence), 5)),
            notes=notes.strip(),
        )
        impression_service.save(item)
        return RedirectResponse(f"/impressions?name={quote(item.name)}&saved=1", status_code=303)

    @router.post("/settings")
    async def save_settings(
        request: Request,
        site_title: str = Form("小窝"),
        brand_avatar_url: str = Form(""),
        search_default_top_k: int = Form(5),
        search_snippet_chars: int = Form(180),
        memory_recall_enabled: str = Form(""),
        memory_recall_policy: str = Form("conservative"),
        enable_diary_module: str = Form(""),
        diary_archive_granularity: str = Form("day"),
        allow_media_refs: str = Form(""),
        media_storage_strategy: str = Form("copy"),
        show_impression_prompt: str = Form(""),
        active_frontend_style: str = Form("default"),
        enabled_official_modules: list[str] = Form(default=[]),
        enabled_custom_modules: list[str] = Form(default=[]),
        enabled_custom_extensions: list[str] = Form(default=[]),
        enabled_appearance_modules: list[str] = Form(default=[]),
        custom_webui_dir: str = Form(""),
        backup_custom_before_update: str = Form(""),
        impression_prompt: str = Form(""),
    ):
        redirect = require_login(request)
        if redirect:
            return redirect
        if not settings_store:
            return RedirectResponse("/settings?error=settings-store-unavailable", status_code=303)
        current_settings = settings_store.load()
        previous_appearance_modules = current_settings.enabled_appearance_modules
        current_settings.site_title = site_title.strip() or "小窝"
        current_settings.brand_avatar_url = brand_avatar_url.strip()
        current_settings.enable_diary_module = enable_diary_module == "on"
        current_settings.search_default_top_k = search_default_top_k
        current_settings.search_snippet_chars = search_snippet_chars
        current_settings.memory_recall_enabled = memory_recall_enabled == "on"
        current_settings.memory_recall_policy = memory_recall_policy
        current_settings.diary_archive_granularity = diary_archive_granularity
        current_settings.allow_media_refs = allow_media_refs == "on"
        current_settings.media_storage_strategy = media_storage_strategy
        current_settings.show_impression_prompt = show_impression_prompt == "on"
        current_settings.active_frontend_style = active_frontend_style.strip() or "default"
        current_settings.enabled_official_modules = enabled_official_modules
        current_settings.enabled_custom_modules = enabled_custom_modules
        current_settings.enabled_custom_extensions = enabled_custom_extensions
        current_settings.enabled_appearance_modules = enabled_appearance_modules or previous_appearance_modules
        current_settings.appearance_modules_initialized = True
        current_settings.custom_webui_dir = custom_webui_dir.strip()
        current_settings.backup_custom_before_update = backup_custom_before_update == "on"
        current_settings.impression_prompt = impression_prompt.strip()
        settings_store.save(current_settings)
        return RedirectResponse("/settings?saved=1", status_code=303)

    @router.post("/settings/security")
    async def save_security_settings(
        request: Request,
        admin_password: str = Form(""),
        bot_api_token: str = Form(""),
        generate_bot_api_token: str = Form(""),
        external_api_enabled: str = Form(""),
    ):
        redirect = require_login(request)
        if redirect:
            return redirect
        if not security_store:
            return RedirectResponse("/settings?error=security-store-unavailable", status_code=303)
        token = security_store.generate_token() if generate_bot_api_token == "on" else bot_api_token.strip()
        saved_security = security_store.update(
            admin_password=admin_password.strip() or None,
            bot_api_token=token,
            external_api_enabled=external_api_enabled == "on",
        )
        if web_auth:
            web_auth.admin_password = saved_security.admin_password
            web_auth.session_secret = saved_security.bot_api_token or "development-session-secret"
        return RedirectResponse("/settings?saved=1", status_code=303)

    @router.post("/settings/version/check")
    async def check_version(request: Request):
        redirect = require_login(request)
        if redirect:
            return redirect
        if not version_service:
            return RedirectResponse("/settings?error=version-service-unavailable", status_code=303)
        try:
            result = version_service.check_latest()
            message = "发现新版本。" if result.update_available else "当前已经是最新版本。"
            return RedirectResponse(
                "/settings?"
                f"version_message={quote(message)}"
                f"&version_current={quote(result.current)}"
                f"&version_latest={quote(result.latest)}"
                f"&update_available={'1' if result.update_available else '0'}",
                status_code=303,
            )
        except Exception as exc:
            return RedirectResponse(f"/settings?error={quote('版本检测失败：' + str(exc))}", status_code=303)

    @router.post("/settings/version/update")
    async def update_version(request: Request):
        redirect = require_login(request)
        if redirect:
            return redirect
        if not version_service:
            return RedirectResponse("/settings?error=version-service-unavailable", status_code=303)
        result = version_service.update()
        target = "version_message" if result.ok else "error"
        detail = result.message
        if result.output:
            detail = f"{detail}\n{result.output}"
        return RedirectResponse(f"/settings?{target}={quote(detail)}", status_code=303)

    @router.get("/settings/export")
    async def export_backup(request: Request):
        redirect = require_login(request)
        if redirect:
            return redirect
        if not backup_service:
            return RedirectResponse("/settings?error=backup-service-unavailable", status_code=303)
        content = backup_service.export_zip(package_type="full")
        return Response(
            content,
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="nest-backup.zip"'},
        )

    @router.post("/settings/import")
    async def import_backup(request: Request, backup_file: UploadFile = File(...), strategy: str = Form("safe")):
        redirect = require_login(request)
        if redirect:
            return redirect
        if not backup_service:
            return RedirectResponse("/settings?error=backup-service-unavailable", status_code=303)
        payload = await backup_file.read()
        try:
            result = backup_service.import_zip(payload, strategy=strategy)
        except Exception as exc:
            return RedirectResponse(f"/settings?error={quote('导入失败：' + str(exc))}", status_code=303)
        indexed = diary_service.rebuild_index() if diary_service else 0
        message = f"导入完成：{result['imported']} 个文件，跳过 {result['skipped']} 个文件，覆盖 {result.get('overwritten', 0)} 个文件，已重建 {indexed} 篇日记索引。"
        return RedirectResponse(f"/settings?version_message={quote(message)}", status_code=303)

    return router


def mount_static(app) -> None:
    if CUSTOM_WEBUI_DIR and (CUSTOM_WEBUI_DIR / "static").exists():
        app.mount("/custom-static", StaticFiles(directory=str(CUSTOM_WEBUI_DIR / "static")), name="custom-static")
    app.mount("/static", StaticFiles(directory=str(DEFAULT_STATIC_DIR)), name="static")


def _split_words(value: str) -> list[str]:
    normalized = value.replace("，", ",").replace("、", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


def _split_lines(value: str) -> list[str]:
    return [item.strip() for item in value.splitlines() if item.strip()]
