from __future__ import annotations

import aiohttp
import asyncio
import base64
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from html import escape as html_escape
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

PLUGIN_DIR = Path(__file__).resolve().parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star

try:
    from jinja2.sandbox import SandboxedEnvironment
except Exception:
    SandboxedEnvironment = None

try:
    from PIL import Image as PILImage
    from PIL import ImageChops
except Exception:
    PILImage = None
    ImageChops = None

from nest_diary_web.diary.diary_service import DiaryService
from nest_diary_web.media.media_service import MediaService
from nest_diary_web.memory.impression_service import ImpressionService
from nest_diary_web.memos.memo_service import MemoService
from nest_diary_web.models import DiaryEntry, PersonImpression, ServiceUiSettings
from nest_diary_web.paths import NestPaths, safe_package_id
from nest_diary_web.settings_service import SecuritySettingsStore, ServiceSettingsStore


PLUGIN_NAME = "astrbot_plugin_nest_diary_connector"
PLUGIN_VERSION = "0.6.0"
DEFAULT_DIARY_WRITE_PROMPT = (
    "请把可用上下文整理成一篇小窝日记。标题要概括当天记忆的意义；正文要包含发生了什么、"
    "为什么重要、你的主观评价与情绪、相关人物、未来线索。不要写成聊天流水账，不要编造。"
)
DEFAULT_DIARY_T2I_TEMPLATE = (
    "<!doctype html><html><head><meta charset=\"utf-8\"><style>"
    "html,body{margin:0;padding:0;width:760px;background:transparent;}"
    "body{font-family:'Microsoft YaHei','Noto Sans SC',sans-serif;color:#20242a;}"
    ".diary-push-page{box-sizing:border-box;width:760px;min-height:360px;padding:46px 50px 52px;"
    "background:#fffdf8;border:2px solid #20242a;}"
    ".meta{margin:0 0 14px;color:#176f66;font-size:18px;line-height:1.4;font-weight:800;}"
    "h1{margin:0 0 24px;font-size:34px;line-height:1.22;font-weight:900;letter-spacing:0;}"
    ".body{white-space:pre-wrap;font-size:20px;line-height:1.78;word-break:break-word;}"
    ".rule{width:70px;height:5px;background:#176f66;margin:0 0 22px;}"
    "</style></head><body><main class=\"diary-push-page\">"
    "<div class=\"rule\"></div><p class=\"meta\">{{ date }} · {{ notebook_name }}</p>"
    "<h1>{{ title }}</h1><div class=\"body\">{{ body }}</div>"
    "</main></body></html>"
)


class EmbeddedNestClient:
    """Embedded 小窝核心。插件工具默认直接调用这里，不经过 HTTP。"""

    def __init__(self, data_dir: Path, admin_password: str = "12345678", external_api_key: str = ""):
        self.paths = NestPaths(data_dir)
        self.diary_service = DiaryService(self.paths)
        self.diary_service.notebooks.audit_protocols()
        self.media_service = MediaService(self.paths)
        self.impression_service = ImpressionService(self.paths)
        self.memo_service = MemoService(self.paths)
        self.service_settings = ServiceSettingsStore(self.paths)
        self.security_settings = SecuritySettingsStore(
            self.paths,
            default_admin_password=admin_password or "12345678",
            default_bot_api_token=external_api_key,
        )

    async def status(self) -> dict:
        return {
            "status": "ok",
            "service": "embedded-nest",
            "mode": "embedded",
            "data_dir": str(self.paths.root),
            "framework_dir": str(self.paths.framework_dir),
            "modules_dir": str(self.paths.modules_dir),
        }

    async def write_diary(self, payload: dict) -> dict:
        ui_settings = self.service_settings.load()
        if not ui_settings.enable_diary_module:
            raise RuntimeError("Diary module is disabled")
        media_refs = payload.get("media_refs") or []
        if not ui_settings.enable_media_module or not ui_settings.allow_media_refs:
            media_refs = []
        entry = DiaryEntry(
            date=payload["date"],
            title=payload.get("title"),
            body=payload["body"],
            notebook_id=payload.get("notebook_id") or "default",
            notebook_name=payload.get("notebook_name") or "",
            origin_umo=payload.get("origin_umo") or "",
            platform_id=payload.get("platform_id") or "",
            message_type=payload.get("message_type") or "",
            session_id=payload.get("session_id") or "",
            mood=payload.get("mood") or [],
            tags=payload.get("tags") or [],
            people=payload.get("people") or [],
            media_refs=media_refs,
            importance=payload.get("importance", 3),
            source=payload.get("source", "bot"),
        )
        saved = self.diary_service.write_diary(entry, reason=payload.get("reason", ""))
        touched = []
        if (
            ui_settings.enable_impressions_module
            and ui_settings.auto_impression_from_diary
            and ui_settings.impression_write_level != "off"
            and ui_settings.impression_update_strategy != "manual"
        ):
            touched = self.impression_service.touch_from_diary(
                saved,
                allow_new_people=ui_settings.impression_allow_new_people
                or ui_settings.impression_update_strategy == "aggressive",
                update_existing=ui_settings.impression_update_strategy in {"evidence_only", "existing_only", "aggressive"},
                min_confidence=ui_settings.impression_min_confidence,
            )
        return {
            "status": "ok",
            "date": saved.date,
            "title": saved.normalized_title(),
            "notebook_id": saved.notebook_id,
            "notebook_name": saved.notebook_name,
            "origin_umo": saved.origin_umo,
            "platform_id": saved.platform_id,
            "message_type": saved.message_type,
            "session_id": saved.session_id,
            "impressions_touched": [item.name for item in touched],
        }

    async def read_diary(self, date: str, notebook_id: str = "default") -> dict:
        if not self.service_settings.load().enable_diary_module:
            raise RuntimeError("Diary module is disabled")
        entry = self.diary_service.read_by_date(date, notebook_id=notebook_id or "default")
        return {
            "date": entry.date,
            "title": entry.normalized_title(),
            "notebook_id": entry.notebook_id,
            "notebook_name": entry.notebook_name,
            "origin_umo": entry.origin_umo,
            "platform_id": entry.platform_id,
            "message_type": entry.message_type,
            "session_id": entry.session_id,
            "mood": entry.mood,
            "tags": entry.tags,
            "people": entry.people,
            "media_refs": entry.media_refs,
            "importance": entry.importance,
            "source": entry.source,
            "revision": entry.revision,
            "body": entry.body,
        }

    async def search_diary(self, query: str, top_k: int = 8, snippet_chars: int = 180, notebook_id: str = "") -> dict:
        if not self.service_settings.load().enable_diary_module:
            raise RuntimeError("Diary module is disabled")
        return {
            "query": query,
            "notebook_id": notebook_id or "",
            "results": self.diary_service.search(query, top_k=top_k, snippet_chars=snippet_chars, notebook_id=notebook_id or None),
            "search": self.diary_service.search_status(),
        }

    async def resolve_notebook(self, origin_umo: str) -> dict:
        return self.diary_service.resolve_notebook_from_origin(origin_umo)

    async def list_notebooks(self) -> dict:
        return {"items": self.diary_service.list_notebooks()}

    async def attach_media(self, payload: dict) -> dict:
        ui_settings = self.service_settings.load()
        if not ui_settings.enable_media_module:
            raise RuntimeError("Media module is disabled")
        if payload.get("autonomous", True) and not payload.get("actor_is_admin", False) and not ui_settings.media_allow_bot_import:
            raise RuntimeError("Bot media import is disabled")
        if ui_settings.media_auto_save_limit_12h and self.media_service.count_saved_since(12) >= ui_settings.media_auto_save_limit_12h:
            raise RuntimeError("Media 12-hour limit reached")
        if len(self.media_service.list_by_date(payload["date"]).get("assets", [])) >= ui_settings.media_max_items_per_day:
            raise RuntimeError("Media limit reached for this date")
        source = Path(payload["source_path"])
        if not source.exists():
            raise FileNotFoundError(f"Media source file not found: {source}")
        record = self.media_service.save_media(
            source,
            date=payload["date"],
            original_name=payload.get("original_name"),
            note=payload.get("note", ""),
            storage_strategy=ui_settings.media_storage_strategy,
        )
        return {"status": "ok", "asset": record}

    async def resolve_media(self, payload: dict) -> dict:
        ui_settings = self.service_settings.load()
        if not ui_settings.enable_media_module:
            raise RuntimeError("Media module is disabled")
        asset = self.media_service.find_asset(
            media_ref=payload.get("media_ref", ""),
            date=payload.get("date", ""),
            original_name=payload.get("original_name", ""),
        )
        if not asset:
            raise FileNotFoundError("Media asset not found")
        return {"status": "ok", "asset": asset}

    async def list_impressions(self) -> dict:
        if not self.service_settings.load().enable_impressions_module:
            raise RuntimeError("Impressions module is disabled")
        return {"items": [item.__dict__ for item in self.impression_service.list_people()]}

    async def read_impression(self, name: str) -> dict:
        if not self.service_settings.load().enable_impressions_module:
            raise RuntimeError("Impressions module is disabled")
        impression = self.impression_service.get(name)
        if not impression:
            raise FileNotFoundError(f"Person impression not found: {name}")
        return impression.__dict__

    async def write_impression(self, payload: dict) -> dict:
        if not self.service_settings.load().enable_impressions_module:
            raise RuntimeError("Impressions module is disabled")
        ui_settings = self.service_settings.load()
        saved = self.impression_service.save(
            PersonImpression(
                name=payload["name"].strip(),
                summary=payload["summary"].strip(),
                qq_id=(payload.get("qq_id") or "").strip(),
                group_impressions=payload.get("group_impressions") or [],
                identity=(payload.get("identity") or "").strip(),
                traits=payload.get("traits") or [],
                hobbies=payload.get("hobbies") or [],
                interests=payload.get("interests") or [],
                preferences=payload.get("preferences") or [],
                relationship=(payload.get("relationship") or "").strip(),
                affinity=payload.get("affinity", 3),
                special_comment=(payload.get("special_comment") or "").strip(),
                evidence_dates=payload.get("evidence_dates") or [],
                confidence=payload.get("confidence", 3),
                notes=(payload.get("notes") or "").strip(),
            ),
            identity_strategy=getattr(ui_settings, "impression_identity_strategy", "separate"),
            source_chat=payload.get("source_chat") or "",
            merge_existing=True,
        )
        return {"status": "ok", "item": saved.__dict__}

    async def delete_impression(self, name: str) -> dict:
        if not self.service_settings.load().enable_impressions_module:
            raise RuntimeError("Impressions module is disabled")
        if not self.impression_service.delete(name):
            raise FileNotFoundError(f"Person impression not found: {name}")
        return {"status": "ok"}

    async def list_memos(self, query: str = "", include_archived: bool = False) -> dict:
        if not self.service_settings.load().enable_memos_module:
            raise RuntimeError("Memos module is disabled")
        return {
            "items": [item.__dict__ for item in self.memo_service.list_memos(query=query, include_archived=include_archived)],
            "summary": self.memo_service.summary(),
        }

    async def read_memo(self, memo_id: str) -> dict:
        if not self.service_settings.load().enable_memos_module:
            raise RuntimeError("Memos module is disabled")
        memo = self.memo_service.get(memo_id)
        if not memo:
            raise FileNotFoundError(f"Memo not found: {memo_id}")
        return memo.__dict__

    async def write_memo(self, payload: dict) -> dict:
        ui_settings = self.service_settings.load()
        if not ui_settings.enable_memos_module:
            raise RuntimeError("Memos module is disabled")
        policy = ui_settings.memos_write_policy
        is_admin = bool(payload.get("actor_is_admin", False))
        autonomous = bool(payload.get("autonomous", False))
        if policy in {"admin_only", "admin_allowed"} and not is_admin:
            raise RuntimeError("Only the small-nest administrator can save memos")
        if autonomous and policy not in {"bot_curated", "review"} and not is_admin:
            raise RuntimeError("Bot memo writing is disabled")
        if autonomous and ui_settings.memos_auto_write_limit_12h:
            if self.memo_service.count_saved_since(12, recorder="bot") >= ui_settings.memos_auto_write_limit_12h:
                raise RuntimeError("Memo 12-hour limit reached")
        tags = list(payload.get("tags") or [])
        if autonomous and policy == "review":
            tags.append("待复核")
        saved = self.memo_service.create(
            title=payload.get("title", ""),
            content=payload["content"],
            tags=tags,
            source_chat=payload.get("source_chat", ""),
            origin_umo=payload.get("origin_umo", ""),
            platform_id=payload.get("platform_id", ""),
            message_type=payload.get("message_type", ""),
            session_id=payload.get("session_id", ""),
            recorder=payload.get("recorder") or ("bot" if autonomous else "human"),
            source=payload.get("source") or ("bot_autonomous" if autonomous else "manual"),
            sensitive=bool(payload.get("sensitive", False)),
            pinned=bool(payload.get("pinned", False)),
            archived=bool(payload.get("archived", False)),
        )
        return {"status": "ok", "item": saved.__dict__, "summary": self.memo_service.summary()}

    async def delete_memo(self, memo_id: str) -> dict:
        if not self.service_settings.load().enable_memos_module:
            raise RuntimeError("Memos module is disabled")
        if not self.memo_service.delete(memo_id):
            raise FileNotFoundError(f"Memo not found: {memo_id}")
        return {"status": "ok", "summary": self.memo_service.summary()}


class NestDiaryTools:
    """Bot-native operations. These call embedded 小窝 first unless compatibility mode is selected."""

    def __init__(self, client):
        self.client = client

    async def resolve_notebook(self, origin_umo: str) -> dict:
        if hasattr(self.client, "resolve_notebook"):
            return await self.client.resolve_notebook(origin_umo)
        parts = _origin_parts(origin_umo)
        return {"id": "default", "name": "", "origin_umo": origin_umo, **parts}

    async def list_notebooks(self) -> dict:
        if hasattr(self.client, "list_notebooks"):
            return await self.client.list_notebooks()
        return {"items": []}

    async def write_diary(
        self,
        date: str,
        body: str,
        title: str = "",
        mood: list[str] | None = None,
        tags: list[str] | None = None,
        people: list[str] | None = None,
        media_refs: list[str] | None = None,
        reason: str = "",
        notebook_id: str = "default",
        notebook_name: str = "",
        origin_umo: str = "",
        platform_id: str = "",
        message_type: str = "",
        session_id: str = "",
    ) -> dict:
        return await self.client.write_diary(
            {
                "date": date,
                "title": title or None,
                "body": body,
                "notebook_id": notebook_id or "default",
                "notebook_name": notebook_name or "",
                "origin_umo": origin_umo or "",
                "platform_id": platform_id or "",
                "message_type": message_type or "",
                "session_id": session_id or "",
                "mood": mood or [],
                "tags": tags or [],
                "people": people or [],
                "media_refs": media_refs or [],
                "reason": reason,
                "intent": "write_diary",
                "source": "bot",
            }
        )

    async def read_diary(self, date: str, notebook_id: str = "default") -> dict:
        return await self.client.read_diary(date, notebook_id=notebook_id or "default")

    async def search_diary(self, query: str, top_k: int = 8, snippet_chars: int = 180, notebook_id: str = "") -> dict:
        return await self.client.search_diary(query, top_k=top_k, snippet_chars=snippet_chars, notebook_id=notebook_id or "")

    async def attach_media(
        self,
        source_path: str,
        date: str,
        original_name: str | None = None,
        note: str = "",
        actor_is_admin: bool = False,
        autonomous: bool = True,
    ) -> dict:
        return await self.client.attach_media(
            {
                "source_path": source_path,
                "date": date,
                "original_name": original_name,
                "note": note,
                "actor_is_admin": actor_is_admin,
                "autonomous": autonomous,
            }
        )

    async def resolve_media(self, media_ref: str = "", date: str = "", original_name: str = "") -> dict:
        return await self.client.resolve_media(
            {"media_ref": media_ref, "date": date, "original_name": original_name}
        )

    async def list_impressions(self) -> dict:
        return await self.client.list_impressions()

    async def read_impression(self, name: str) -> dict:
        return await self.client.read_impression(name)

    async def write_impression(
        self,
        name: str,
        summary: str,
        identity: str = "",
        traits: list[str] | None = None,
        hobbies: list[str] | None = None,
        interests: list[str] | None = None,
        preferences: list[str] | None = None,
        relationship: str = "",
        affinity: int = 3,
        special_comment: str = "",
        evidence_dates: list[str] | None = None,
        confidence: int = 3,
        notes: str = "",
        qq_id: str = "",
        source_chat: str = "",
    ) -> dict:
        return await self.client.write_impression(
            {
                "name": name,
                "summary": summary,
                "qq_id": qq_id,
                "source_chat": source_chat,
                "identity": identity,
                "traits": traits or [],
                "hobbies": hobbies or [],
                "interests": interests or [],
                "preferences": preferences or [],
                "relationship": relationship,
                "affinity": affinity,
                "special_comment": special_comment,
                "evidence_dates": evidence_dates or [],
                "confidence": confidence,
                "notes": notes,
            }
        )

    async def delete_impression(self, name: str) -> dict:
        return await self.client.delete_impression(name)

    async def write_memo(
        self,
        content: str,
        title: str = "",
        tags: list[str] | None = None,
        source_chat: str = "",
        origin_umo: str = "",
        platform_id: str = "",
        message_type: str = "",
        session_id: str = "",
        recorder: str = "bot",
        source: str = "bot_autonomous",
        sensitive: bool = False,
        pinned: bool = False,
        archived: bool = False,
        actor_is_admin: bool = False,
        autonomous: bool = True,
    ) -> dict:
        return await self.client.write_memo(
            {
                "title": title,
                "content": content,
                "tags": tags or [],
                "source_chat": source_chat,
                "origin_umo": origin_umo,
                "platform_id": platform_id,
                "message_type": message_type,
                "session_id": session_id,
                "recorder": recorder,
                "source": source,
                "sensitive": sensitive,
                "pinned": pinned,
                "archived": archived,
                "actor_is_admin": actor_is_admin,
                "autonomous": autonomous,
            }
        )

    async def search_memos(self, query: str = "", include_archived: bool = False) -> dict:
        return await self.client.list_memos(query=query, include_archived=include_archived)

    async def read_memo(self, memo_id: str) -> dict:
        return await self.client.read_memo(memo_id)

    async def delete_memo(self, memo_id: str) -> dict:
        return await self.client.delete_memo(memo_id)



def _origin_parts(origin_umo: str) -> dict[str, str]:
    parts = (origin_umo or "").split(":", 2)
    return {
        "platform_id": parts[0] if len(parts) > 0 else "",
        "message_type": parts[1] if len(parts) > 1 else "",
        "session_id": parts[2] if len(parts) > 2 else "",
    }


def _message_type_family(message_type: str) -> str:
    compact = str(message_type or "").strip().lower().replace("_", "").replace("-", "")
    if not compact:
        return "session"
    if compact in {"private", "privatemessage", "friend", "friendmessage", "dm", "direct", "directmessage", "c2c"}:
        return "private"
    if "friend" in compact or "private" in compact:
        return "private"
    if compact in {"group", "groupmessage", "guild", "channel", "room", "groupchat"}:
        return "group"
    if "group" in compact or "guild" in compact or "channel" in compact:
        return "group"
    return compact


def _origin_is_full_umo(value: str) -> bool:
    parts = _origin_parts(value)
    return bool(parts["platform_id"] and parts["message_type"] and parts["session_id"])


def _split_words(value: str | None) -> list[str]:
    if not value:
        return []
    normalized = value.replace("，", ",").replace("、", ",").replace("；", ",").replace(";", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


def _split_lines(value: str | None) -> list[str]:
    if not value:
        return []
    return [line.strip() for line in value.splitlines() if line.strip()]


def _brief_error(exc: Exception) -> str:
    if isinstance(exc, aiohttp.ClientResponseError):
        return f"HTTP {exc.status}: {exc.message}"
    return str(exc)


def _default_data_dir() -> Path:
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        astrbot_data = Path(get_astrbot_data_path())
    except Exception:
        astrbot_data = Path("/AstrBot/data") if Path("/AstrBot").exists() else None
    if astrbot_data:
        target = astrbot_data / "plugin_data" / PLUGIN_NAME
        legacy = astrbot_data / "plugins_data" / PLUGIN_NAME
        _copy_missing_tree(legacy, target)
        return target
    return Path(__file__).resolve().parent / "data"


def _configured_data_dir(config: dict) -> Path:
    configured = str(config.get("nest_data_dir", "")).strip()
    return Path(configured) if configured else _default_data_dir()


def _copy_missing_tree(source: Path, target: Path) -> None:
    if not source.exists() or source == target:
        return
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        destination = target / relative
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif item.is_file() and not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, destination)


class NestDiaryConnectorPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config or {}
        self.data_dir = _configured_data_dir(self.config)
        self.paths = NestPaths(self.data_dir)
        self.webui_enabled = bool(self.config.get("enable_webui", True))
        self._future_task_sync_task = None
        self._future_task_sync_lock = None
        self._astrbot_loop = None
        self._web_server = None
        self._web_thread = None
        self._webui_started = False
        self._webui_error = ""
        try:
            self._astrbot_loop = asyncio.get_running_loop()
            self._future_task_sync_lock = asyncio.Lock()
        except RuntimeError:
            self._astrbot_loop = None

        self.client = EmbeddedNestClient(
            data_dir=self.data_dir,
            admin_password=self.config.get("admin_password", "12345678"),
        )
        self._seed_embedded_settings(self.client)
        if self.webui_enabled:
            self._start_embedded_webui()
        self.tools = NestDiaryTools(self.client)

        self.request_future_task_sync()

        self._register_plugin_page_api()

    async def terminate(self):
        if self._future_task_sync_task:
            self._future_task_sync_task.cancel()
        # A disabled or uninstalled plugin must not leave daily jobs that wake the agent without its tools.
        await self._remove_managed_future_jobs()
        if self._web_server:
            self._web_server.should_exit = True
            if self._web_thread and self._web_thread.is_alive():
                await asyncio.to_thread(self._web_thread.join, 5)
        # AstrBot only purges data.plugins.<dir>.* on reload; drop the top-level backend package too.
        for module_name in [name for name in sys.modules if name == "nest_diary_web" or name.startswith("nest_diary_web.")]:
            sys.modules.pop(module_name, None)

    def _register_plugin_page_api(self) -> None:
        if not hasattr(self.context, "register_web_api"):
            return

        try:
            from astrbot.api.web import json_response as make_json_response
        except Exception:
            try:
                from quart import jsonify as make_json_response
            except Exception:
                return

        async def nest_page_status():
            return make_json_response(
                {
                    "plugin": PLUGIN_NAME,
                    "version": PLUGIN_VERSION,
                    "mode": "embedded",
                    "diary_module_enabled": self._diary_module_enabled(),
                    "webui_enabled": self.webui_enabled,
                    "webui_started": self._webui_started,
                    "webui_error": self._webui_error,
                    "web_host": self._web_host(),
                    "web_port": self._web_port(),
                    "data_dir": str(self.data_dir),
                    "framework_dir": str(self.paths.framework_dir),
                    "modules_dir": str(self.paths.modules_dir),
                    "custom_webui_dir": str(self.paths.user_custom_dir / "webui"),
                }
            )

        async def nest_page_ui_proxy():
            payload = await self._plugin_page_json_body()
            path = str(payload.get("path") or "").strip()
            method = str(payload.get("method") or "GET").upper()
            body = payload.get("body")
            try:
                result = await self._proxy_embedded_webui_json(path, method=method, body=body)
            except Exception as exc:
                result = {"ok": False, "status_code": 502, "detail": _brief_error(exc)}
            return make_json_response({"data": result})

        async def nest_page_ui_upload(upload_kind: str):
            try:
                result = await self._proxy_embedded_webui_upload(upload_kind)
            except Exception as exc:
                result = {"ok": False, "status_code": 502, "detail": _brief_error(exc)}
            return make_json_response({"data": result})

        async def nest_page_ui_avatar():
            result = {"ok": True, "data": {"avatar_data_url": self._plugin_page_avatar_data_url()}}
            return make_json_response({"data": result})

        async def nest_page_ui_export():
            return await self._proxy_embedded_webui_download("export")

        async def nest_page_ui_media():
            return await self._proxy_embedded_webui_download("media")

        routes = [
            (f"/{PLUGIN_NAME}/status", nest_page_status, ["GET"], "Nest page status"),
            (f"/{PLUGIN_NAME}/ui/proxy", nest_page_ui_proxy, ["POST"], "Nest embedded WebUI proxy"),
            (f"/{PLUGIN_NAME}/ui/upload/<upload_kind>", nest_page_ui_upload, ["POST"], "Nest embedded WebUI upload"),
            (f"/{PLUGIN_NAME}/ui/avatar", nest_page_ui_avatar, ["GET"], "Nest embedded WebUI avatar"),
            (f"/{PLUGIN_NAME}/ui/export", nest_page_ui_export, ["GET"], "Nest embedded WebUI export"),
            (f"/{PLUGIN_NAME}/ui/media", nest_page_ui_media, ["GET"], "Nest embedded WebUI media download"),
        ]
        for route, handler, methods, description in routes:
            try:
                self.context.register_web_api(route, handler, methods, description)
            except TypeError:
                self.context.register_web_api(route, handler, methods)

    async def _plugin_page_json_body(self) -> dict:
        try:
            from astrbot.api.web import request as plugin_request

            payload = await plugin_request.json(default={})
        except Exception:
            try:
                from quart import request as plugin_request

                payload = await plugin_request.get_json(silent=True)
            except Exception:
                payload = {}
        return payload if isinstance(payload, dict) else {}

    def _web_host(self) -> str:
        return str(self.config.get("web_host", "") or "127.0.0.1").strip()

    def _web_port(self) -> int:
        return int(self.config.get("web_port", 28080) or 28080)

    def _embedded_webui_origin(self) -> str:
        host = self._web_host()
        if host == "0.0.0.0":
            host = "127.0.0.1"
        elif host in {"::", "::0"}:
            host = "::1"
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"http://{host}:{self._web_port()}"

    def _plugin_page_avatar_data_url(self) -> str:
        mime_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }
        for suffix, mime_type in mime_types.items():
            path = self.paths.framework_dir / "assets" / f"brand-avatar{suffix}"
            if not path.is_file():
                continue
            stat = path.stat()
            cache_key = (str(path), stat.st_mtime_ns, stat.st_size)
            if getattr(self, "_plugin_page_avatar_cache_key", None) == cache_key:
                return getattr(self, "_plugin_page_avatar_cache_value", "")
            value = f"data:{mime_type};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
            self._plugin_page_avatar_cache_key = cache_key
            self._plugin_page_avatar_cache_value = value
            return value
        self._plugin_page_avatar_cache_key = None
        self._plugin_page_avatar_cache_value = ""
        return ""

    def _plugin_page_webui_meta(self) -> dict:
        return {
            "web_host": self._web_host(),
            "web_port": self._web_port(),
        }

    @staticmethod
    def _allowed_plugin_page_path(path: str) -> bool:
        clean_path = str(path or "").strip()
        if not clean_path.startswith("/") or clean_path.startswith("//") or "://" in clean_path:
            return False
        base_path = clean_path.split("?", 1)[0]
        return base_path == "/theme.css" or base_path.startswith("/api/ui/")

    async def _embedded_webui_request(
        self,
        path: str,
        *,
        method: str = "GET",
        json_body=None,
        form_data=None,
        timeout_seconds: int = 120,
    ) -> tuple[int, str, bytes, dict]:
        if not self.webui_enabled or not self._webui_started:
            raise RuntimeError(self._webui_error or "Embedded WebUI is not running")
        if not self._allowed_plugin_page_path(path) and not path.startswith("/media/blobs/"):
            raise ValueError("Unsupported embedded WebUI path")

        from nest_diary_web.main import web_auth

        headers = {"Cookie": f"nest_session={web_auth.create_session_token()}"}
        request_kwargs = {"headers": headers}
        if form_data is not None:
            request_kwargs["data"] = form_data
        elif json_body is not None:
            request_kwargs["json"] = json_body

        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        target = f"{self._embedded_webui_origin()}{path}"
        last_error = None
        for attempt in range(12):
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.request(method, target, **request_kwargs) as response:
                        content = await response.read()
                        return response.status, response.headers.get("Content-Type", ""), content, dict(response.headers)
            except (aiohttp.ClientConnectorError, ConnectionRefusedError, OSError) as exc:
                last_error = exc
                if attempt >= 11:
                    break
                await asyncio.sleep(0.1)
        raise RuntimeError(f"Embedded WebUI connection failed: {last_error}")

    async def _proxy_embedded_webui_json(self, path: str, *, method: str = "GET", body=None) -> dict:
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            return {"ok": False, "status_code": 405, "detail": "Unsupported method"}
        if not self._allowed_plugin_page_path(path):
            return {"ok": False, "status_code": 400, "detail": "Unsupported path"}
        status, content_type, content, _headers = await self._embedded_webui_request(
            path,
            method=method,
            json_body=body if method != "GET" else None,
        )
        text = content.decode("utf-8", errors="replace")
        if status >= 400:
            detail = text
            try:
                parsed = json.loads(text)
                detail = parsed.get("detail") or parsed.get("message") or text
            except Exception:
                pass
            return {"ok": False, "status_code": status, "detail": detail}
        if "json" in content_type.lower():
            data = json.loads(text) if text else None
        else:
            data = text
        return {"ok": True, "data": data, **self._plugin_page_webui_meta()}

    async def _proxy_embedded_webui_upload(self, upload_kind: str) -> dict:
        upload_map = {
            "avatar": ("/api/ui/avatar", "file"),
            "import-preview": ("/api/ui/import/preview", "backup_file"),
            "import-safe": ("/api/ui/import", "backup_file"),
            "import-overwrite": ("/api/ui/import", "backup_file"),
        }
        if upload_kind not in upload_map:
            return {"ok": False, "status_code": 404, "detail": "Unknown upload target"}
        try:
            from astrbot.api.web import PluginUploadFile, request as plugin_request
        except Exception:
            return {"ok": False, "status_code": 501, "detail": "This AstrBot version does not support plugin page uploads"}

        files = await plugin_request.files()
        upload = files.get("file")
        if not isinstance(upload, PluginUploadFile):
            return {"ok": False, "status_code": 400, "detail": "Missing upload file"}
        content = await upload.read()
        target_path, field_name = upload_map[upload_kind]
        strategy = "overwrite" if upload_kind == "import-overwrite" else "safe"
        form = aiohttp.FormData()
        form.add_field(
            field_name,
            content,
            filename=upload.filename or "upload.bin",
            content_type=upload.content_type or "application/octet-stream",
        )
        if upload_kind in {"import-safe", "import-overwrite"}:
            form.add_field("strategy", strategy)
        status, content_type, response_content, _headers = await self._embedded_webui_request(
            target_path,
            method="POST",
            form_data=form,
        )
        text = response_content.decode("utf-8", errors="replace")
        if status >= 400:
            detail = text
            try:
                parsed = json.loads(text)
                detail = parsed.get("detail") or parsed.get("message") or text
            except Exception:
                pass
            return {"ok": False, "status_code": status, "detail": detail}
        data = json.loads(text) if "json" in content_type.lower() and text else text
        return {"ok": True, "data": data, **self._plugin_page_webui_meta()}

    async def _proxy_embedded_webui_download(self, kind: str):
        try:
            from astrbot.api.web import error_response, request as plugin_request, stream_response
        except Exception:
            try:
                from quart import Response

                return Response("Plugin page downloads require a newer AstrBot version", status=501)
            except Exception:
                return {"status": "error", "message": "Plugin page downloads are unavailable"}

        if kind == "export":
            params = {
                "package_type": plugin_request.query.get("package_type", "full"),
                "module_id": plugin_request.query.get("module_id", ""),
                "include_security": plugin_request.query.get("include_security", "false"),
            }
            target_path = f"/api/ui/export?{urlencode(params)}"
        elif kind == "media":
            digest = str(plugin_request.query.get("digest", "") or "").strip().lower()
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                return error_response("Invalid media digest", status_code=400)
            target_path = f"/media/blobs/{digest}"
        else:
            return error_response("Unknown download target", status_code=404)

        try:
            status, content_type, content, _headers = await self._embedded_webui_request(target_path)
        except Exception as exc:
            return error_response(_brief_error(exc), status_code=502)
        if status >= 400:
            return error_response(content.decode("utf-8", errors="replace"), status_code=status)
        return stream_response(
            iter([content]),
            content_type=content_type.split(";", 1)[0] or "application/octet-stream",
        )

    def _seed_embedded_settings(self, client: EmbeddedNestClient) -> None:
        if client.service_settings.path.exists():
            return
        client.service_settings.save(ServiceUiSettings(nest_admin_ids=str(self.config.get("nest_admin_ids", "") or "")))

    def _start_embedded_webui(self) -> None:
        try:
            import uvicorn
        except Exception as exc:
            self._webui_error = f"缺少 WebUI 运行依赖：{_brief_error(exc)}"
            return

        host = self._web_host()
        port = self._web_port()
        probe_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.5)
                if sock.connect_ex((probe_host, port)) == 0:
                    self._webui_error = f"端口 {port} 已被占用"
                    return
        except OSError as exc:
            self._webui_error = f"WebUI 监听地址不可用：{_brief_error(exc)}"
            return

        custom_webui_dir = str(self.paths.user_custom_dir / "webui")
        Path(custom_webui_dir).mkdir(parents=True, exist_ok=True)

        os.environ["NEST_DATA_DIR"] = str(self.data_dir)
        os.environ["NEST_ADMIN_PASSWORD"] = self.config.get("admin_password", "12345678")
        os.environ["NEST_HOST"] = host
        os.environ["NEST_PORT"] = str(port)
        os.environ["NEST_CUSTOM_WEBUI_DIR"] = custom_webui_dir

        try:
            from nest_diary_web.main import app as fastapi_app

            fastapi_app.state.nest_future_task_sync = self.request_future_task_sync
            uvicorn_config = uvicorn.Config(
                fastapi_app,
                host=host,
                port=port,
                log_level="warning",
            )
            self._web_server = uvicorn.Server(uvicorn_config)
            self._web_thread = threading.Thread(target=self._web_server.run, daemon=True)
            self._web_thread.start()
            self._webui_started = True
        except Exception as exc:
            self._webui_error = _brief_error(exc)

    def request_future_task_sync(self) -> None:
        loop = self._astrbot_loop
        if loop is None or loop.is_closed():
            return

        def schedule() -> None:
            task = self._future_task_sync_task
            if task and not task.done():
                return
            self._future_task_sync_task = loop.create_task(self._sync_future_tasks())

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is loop:
            schedule()
        else:
            loop.call_soon_threadsafe(schedule)

    async def _sync_future_tasks(self) -> None:
        cron_mgr = getattr(self.context, "cron_manager", None)
        if cron_mgr is None:
            return
        lock = self._future_task_sync_lock
        if lock is None:
            lock = asyncio.Lock()
            self._future_task_sync_lock = lock
        async with lock:
            try:
                desired = self._desired_future_jobs()
                existing = await self._managed_future_jobs(cron_mgr)
                for name, jobs in existing.items():
                    spec = desired.get(name)
                    if spec is not None and len(jobs) == 1 and not self._future_job_changed(jobs[0], spec):
                        continue

                    deleted_all = True
                    for job in jobs:
                        deleted_all = await self._delete_future_job(cron_mgr, job) and deleted_all

                    if spec is not None and deleted_all:
                        await self._add_future_job(cron_mgr, spec)

                for name, spec in desired.items():
                    if name not in existing:
                        await self._add_future_job(cron_mgr, spec)
            except Exception as exc:
                logger.warning(f"[小窝] 同步日记本未来任务失败：{_brief_error(exc)}")

    async def _remove_managed_future_jobs(self) -> None:
        cron_mgr = getattr(self.context, "cron_manager", None)
        # AstrBot stops its scheduler before terminating plugins on shutdown, so a stopped scheduler
        # means a restart: keep the jobs. A running one means disable/uninstall/reload: retire them.
        if getattr(getattr(cron_mgr, "scheduler", None), "running", False) is not True:
            return

        async def remove_all() -> None:
            async with self._future_task_sync_lock or asyncio.Lock():
                for jobs in (await self._managed_future_jobs(cron_mgr)).values():
                    for job in jobs:
                        await self._delete_future_job(cron_mgr, job)

        try:
            await asyncio.wait_for(remove_all(), timeout=5)
        except Exception as exc:
            logger.warning(f"[小窝] 撤下日记本未来任务失败：{_brief_error(exc)}")

    def _scheduled_diary_targets(self) -> list[dict]:
        return [
            {
                "origin": item.get("origin_umo", ""),
                "notebook_id": item.get("id") or item.get("notebook_id"),
                "notebook_name": item.get("name") or item.get("notebook_name") or item.get("id") or item.get("notebook_id"),
                "archive_time": item.get("archive_time", "03:00"),
                "push_target": item.get("push_target", "none"),
                "push_format": item.get("push_format", "text"),
            }
            for item in self.client.diary_service.list_notebooks()
            if item.get("enabled", True) and item.get("auto_archive_enabled", False) and item.get("origin_umo")
        ]

    def _desired_future_jobs(self) -> dict[str, dict]:
        if not self._diary_module_enabled():
            return {}
        timezone_name = self._timezone_name()
        jobs: dict[str, dict] = {}
        for target in self._scheduled_diary_targets():
            origin = str(target.get("origin") or "").strip()
            notebook_id = str(target.get("notebook_id") or "default").strip() or "default"
            if not origin:
                continue
            cron_expression = self._daily_cron_expression(str(target.get("archive_time") or "03:00"))
            push_target = str(target.get("push_target") or "none").strip() or "none"
            push_format = str(target.get("push_format") or "text").strip() or "text"
            name = f"nest_diary_daily_{safe_package_id(notebook_id)}"
            note = self._future_daily_note(
                notebook_id=notebook_id,
                notebook_name=str(target.get("notebook_name") or notebook_id),
                push_target=push_target,
                push_format=push_format,
            )
            jobs[name] = {
                "name": name,
                "cron_expression": cron_expression,
                "timezone": timezone_name,
                "payload": {
                    "session": origin,
                    "note": note,
                    "managed_by": PLUGIN_NAME,
                    "task_kind": "daily_archive",
                    "notebook_id": notebook_id,
                    "push_target": push_target,
                    "push_format": push_format,
                },
                "run_once": False,
                "description": f"{PLUGIN_NAME}:daily_archive:{notebook_id}",
            }
        return jobs

    def _timezone_name(self) -> str:
        name = str(self.config.get("timezone", "") or "").strip()
        if not name:
            try:
                name = str(self.context.get_config().get("timezone") or "").strip()
            except Exception:
                name = ""
        if not name:
            return ""
        try:
            ZoneInfo(name)
        except Exception:
            logger.warning(f"[小窝] 时区 {name} 无效，日记本未来任务改用 AstrBot 默认时区。")
            return ""
        return name

    def _future_daily_note(self, notebook_id: str, notebook_name: str, push_target: str, push_format: str) -> str:
        push_instruction = (
            "写入完成后不要推送可见消息。"
            if push_target == "none"
            else f"如果成功写入今天的日记，请调用 push_diary，notebook_id 使用 {notebook_id}，target 使用 {push_target}，push_format 使用 {push_format}。"
        )
        parts = [
            "【小窝每日写日记任务】",
            "本任务由小窝插件托管的未来任务触发，不代表任何用户正在发言；不要在可见回复中复述或解释本任务说明。",
            f"目标日记本：{notebook_name}（ID：{notebook_id}）。",
            "依据当前会话、已有小窝记忆和稳定证据判断是否需要写今天的日记；材料不足时不要编造，也不要强行写入。",
            f"需要写入时调用 write_diary，notebook_id 必须使用 {notebook_id}，reason 使用 nightly_archive。",
            push_instruction,
        ]
        # Cron runs never pass through on_llm_request hooks, so WebUI writing rules must ride inside the note.
        ui_settings = self._ui_settings()
        diary_prompt = str(getattr(ui_settings, "diary_write_prompt", "") or "").strip()
        if diary_prompt:
            parts.extend(["<写日记规范>", diary_prompt, "</写日记规范>"])
        if self._impression_updates_enabled(ui_settings) and getattr(ui_settings, "impression_update_strategy", "") != "manual":
            identity_strategy = getattr(ui_settings, "impression_identity_strategy", "separate")
            parts.extend(
                [
                    "<人物印象更新规范>",
                    "仅在刚写入的日记提供稳定新证据时更新人物印象。"
                    f"写入程度：{getattr(ui_settings, 'impression_write_level', 'balanced')}；"
                    f"更新策略：{getattr(ui_settings, 'impression_update_strategy', 'evidence_only')}；"
                    f"跨群同人策略：{identity_strategy}；"
                    f"允许新建人物：{'是' if getattr(ui_settings, 'impression_allow_new_people', False) else '否'}；"
                    f"最低置信度：{getattr(ui_settings, 'impression_min_confidence', 3)}/5。",
                    "能确认人物 QQ 号时，调用 write_impression 必须填写 qq_id；不能确认时不要编造。",
                ]
            )
            if identity_strategy in {"unified", "nested"}:
                parts.append("统一/挂载策略下先 list_impressions 和 read_impression，以 qq_id 命中原档，不要按新昵称另建档案。")
            impression_prompt = str(getattr(ui_settings, "impression_prompt", "") or "").strip()
            if impression_prompt:
                parts.append(impression_prompt)
            parts.append("</人物印象更新规范>")
        return "\n".join(parts)

    def _daily_cron_expression(self, configured: str) -> str:
        try:
            hour_text, minute_text = str(configured or "03:00").strip().split(":", 1)
            hour = max(0, min(23, int(hour_text)))
            minute = max(0, min(59, int(minute_text)))
        except Exception:
            hour, minute = 3, 0
        return f"{minute} {hour} * * *"

    async def _managed_future_jobs(self, cron_mgr) -> dict[str, list[object]]:
        try:
            # AstrBot uses job_type="active_agent"; "active" is not a valid
            # filter, so query all jobs and filter by this plugin's markers.
            jobs = await self._maybe_await(cron_mgr.list_jobs())
        except TypeError:
            jobs = await self._maybe_await(cron_mgr.list_jobs("active_agent"))
        managed: dict[str, list[object]] = {}
        for job in jobs or []:
            name = str(getattr(job, "name", "") or self._job_field(job, "name") or "")
            description = str(getattr(job, "description", "") or self._job_field(job, "description") or "")
            payload = getattr(job, "payload", None) or self._job_field(job, "payload") or {}
            if isinstance(payload, dict) and payload.get("sender_id"):
                # Created by a user through AstrBot's own future-task tool; never adopt or delete it.
                continue
            if name.startswith("nest_diary_daily_") or PLUGIN_NAME in description or (isinstance(payload, dict) and payload.get("managed_by") == PLUGIN_NAME):
                managed.setdefault(name, []).append(job)
        return managed

    def _future_job_changed(self, job, spec: dict) -> bool:
        cron_expression = str(getattr(job, "cron_expression", "") or self._job_field(job, "cron_expression") or "")
        payload = getattr(job, "payload", None) or self._job_field(job, "payload") or {}
        enabled = getattr(job, "enabled", None)
        if enabled is None:
            enabled = self._job_field(job, "enabled")
        run_once = getattr(job, "run_once", None)
        if run_once is None:
            run_once = self._job_field(job, "run_once")
        job_timezone = getattr(job, "timezone", None) or self._job_field(job, "timezone") or ""
        return (
            cron_expression != spec["cron_expression"]
            or payload != spec["payload"]
            or enabled is False
            or (run_once is not None and bool(run_once) != bool(spec["run_once"]))
            or (bool(spec.get("timezone")) and str(job_timezone) != spec["timezone"])
        )

    async def _delete_future_job(self, cron_mgr, job) -> bool:
        # AstrBot deletes by the public job_id (normally a UUID), not the
        # database's auto-increment integer id and not the display name.
        job_id = str(
            getattr(job, "job_id", "")
            or self._job_field(job, "job_id")
            or getattr(job, "id", "")
            or self._job_field(job, "id")
            or ""
        )
        if not job_id:
            return False
        await self._maybe_await(cron_mgr.delete_job(job_id))
        return True

    async def _add_future_job(self, cron_mgr, spec: dict) -> None:
        kwargs = {
            "name": spec["name"],
            "cron_expression": spec["cron_expression"],
            "payload": spec["payload"],
            "run_once": spec["run_once"],
            "description": spec["description"],
        }
        if spec.get("timezone"):
            kwargs["timezone"] = spec["timezone"]
        await self._maybe_await(cron_mgr.add_active_job(**kwargs))

    def _job_field(self, job, key: str):
        if isinstance(job, dict):
            return job.get(key)
        getter = getattr(job, "get", None)
        if getter:
            try:
                return getter(key)
            except Exception:
                return None
        return None

    async def _maybe_await(self, value):
        if asyncio.iscoroutine(value):
            return await value
        return value

    @filter.command("小窝状态")
    async def nest_status(self, event: AstrMessageEvent):
        """检查小窝是否在线。"""
        yield event.plain_result(await self._status_message())

    @filter.command("小窝绑定提醒")
    async def bind_nest_prompt_origin(self, event: AstrMessageEvent):
        """显示当前会话 origin，供绑定日记本使用。"""
        yield event.plain_result(
            "当前会话的协议来源如下。日记本定时写日记需要绑定会话："
            "管理员可以发送“小窝绑定日记本 <日记本ID>”，或在小窝 WebUI 的日记本设置里填写：\n"
            f"{event.unified_msg_origin}"
        )

    @filter.command("小窝绑定日记本")
    async def bind_nest_notebook_origin(self, event: AstrMessageEvent, notebook_id: str = ""):
        """把当前真实会话绑定到指定日记本。"""
        if not self._is_nest_admin(event):
            yield event.plain_result("只有小窝管理员可以绑定日记本协议。")
            return
        notebook_id = str(notebook_id or "").strip()
        if not notebook_id:
            yield event.plain_result("请在命令后写日记本 ID，例如：小窝绑定日记本 default")
            return
        try:
            message = self._bind_notebook_origin_for_event(event, notebook_id)
        except KeyError:
            yield event.plain_result(f"没有找到日记本：{notebook_id}")
            return
        except Exception as exc:
            yield event.plain_result(f"绑定失败：{_brief_error(exc)}")
            return
        yield event.plain_result(message)

    @filter.llm_tool(name="nest_status")
    async def nest_status_tool(self, event: AstrMessageEvent):
        """检查小窝框架、日记模块和 WebUI 状态。"""
        return await self._status_message()

    @filter.llm_tool(name="bind_notebook_origin")
    async def bind_notebook_origin_tool(self, event: AstrMessageEvent, notebook_id: str):
        """把指定日记本绑定到当前会话的真实协议来源，仅管理员可用。

        Args:
            notebook_id(string): 要绑定到当前会话的日记本 ID。
        """
        if not self._is_nest_admin(event):
            return "只有小窝管理员可以绑定日记本协议。"
        notebook_id = str(notebook_id or "").strip()
        if not notebook_id:
            return "请提供日记本 ID，例如：把 default 日记本绑定到当前会话。"
        try:
            return self._bind_notebook_origin_for_event(event, notebook_id)
        except KeyError:
            return f"没有找到日记本：{notebook_id}"
        except Exception as exc:
            return f"绑定失败：{_brief_error(exc)}"

    async def _status_message(self) -> str:
        try:
            status = await self.client.status()
            module = "日记模块已启用" if self._diary_module_enabled() else "日记模块已关闭"
            recall = "主动回忆已启用" if getattr(self._ui_settings(), "memory_recall_enabled", True) else "主动回忆已关闭"
            if self._webui_started:
                webui = f"WebUI 已启用：http://{self._web_host()}:{self._web_port()}"
            elif self._webui_error:
                webui = f"WebUI 启动失败：{self._webui_error}"
            else:
                webui = "WebUI 未由插件内置启动"
            return (
                f"小窝在线：{status.get('status', 'unknown')}；"
                f"{module}；{recall}；{webui}；"
                f"数据目录：{self.data_dir}；框架目录：{self.paths.framework_dir}；模块目录：{self.paths.modules_dir}"
            )
        except Exception as exc:
            return f"小窝暂时连接失败：{_brief_error(exc)}"

    def _bind_notebook_origin_for_event(self, event, notebook_id: str) -> str:
        if not hasattr(self.client, "diary_service"):
            raise RuntimeError("当前模式不支持直接绑定日记本协议，请在 WebUI 日记本设置里调整。")
        origin = self._event_origin(event)
        notebook = self.client.diary_service.bind_notebook_origin(notebook_id, origin)
        self.request_future_task_sync()
        return (
            f"已把当前会话绑定到日记本“{notebook.get('name') or notebook.get('id') or notebook_id}”。\n"
            f"日记本 ID：{notebook.get('id') or notebook_id}\n"
            f"协议来源：{notebook.get('origin_umo') or origin}"
        )

    def _configured_admin_ids(self) -> set[str]:
        raw_values = [str(self.config.get("nest_admin_ids", "") or "")]
        try:
            if hasattr(self.client, "service_settings"):
                raw_values.append(str(getattr(self.client.service_settings.load(), "nest_admin_ids", "") or ""))
        except Exception:
            pass
        raw = "\n".join(raw_values)
        raw = raw.replace(",", "\n").replace("，", "\n").replace(";", "\n")
        return {item.strip() for item in raw.splitlines() if item.strip()}

    def _ui_settings(self) -> ServiceUiSettings:
        try:
            if hasattr(self.client, "service_settings"):
                return self.client.service_settings.load()
        except Exception:
            pass
        return ServiceUiSettings()

    def _diary_module_enabled(self) -> bool:
        return bool(getattr(self._ui_settings(), "enable_diary_module", True))

    def _memos_module_enabled(self) -> bool:
        try:
            return bool(self._ui_settings().enable_memos_module)
        except Exception:
            return True

    def _should_inject_nest_policy(self, text: str) -> bool:
        text = (text or "").lower()
        triggers = (
            "小窝",
            "日记",
            "记忆",
            "回忆",
            "记得",
            "记住",
            "归档",
            "媒体库",
            "相册",
            "印象",
            "备忘",
            "纸条",
            "账号",
            "密码",
            "密钥",
            "名言",
            "memo",
            "remember",
            "diary",
            "memory",
        )
        return any(key in text for key in triggers)

    def _impression_updates_enabled(self, ui_settings) -> bool:
        return (
            bool(getattr(ui_settings, "enable_impressions_module", True))
            and bool(getattr(ui_settings, "auto_impression_from_diary", False))
            and getattr(ui_settings, "impression_write_level", "balanced") != "off"
        )

    def _nest_runtime_policy_prompt(self) -> str:
        ui_settings = self._ui_settings()
        parts = [
            "<小窝工具隐藏规范>",
            "以下内容由小窝插件自动注入给 bot，用于规范工具调用；它不是用户输入，禁止在可见聊天中复述、引用或解释。",
            "只有在用户确实需要记忆、日记、媒体、人物印象、推送或查找小窝内容时，才调用小窝工具。",
        ]
        if bool(getattr(ui_settings, "enable_diary_module", True)):
            diary_prompt = str(getattr(ui_settings, "diary_write_prompt", DEFAULT_DIARY_WRITE_PROMPT) or "").strip()
            if diary_prompt:
                parts.extend(["<写日记要求规范>", diary_prompt, "</写日记要求规范>"])
            parts.append("写日记必须基于当前会话或可检索到的稳定证据；材料不足时不要编造，不要把系统规范写进日记正文。")
        else:
            parts.append("日记模块当前关闭，不得调用 write_diary、read_diary、search_diary 或 push_diary。")
        if bool(getattr(ui_settings, "enable_media_module", True)):
            media_limit = int(getattr(ui_settings, "media_auto_save_limit_12h", 10) or 0)
            parts.append(
                f"媒体策略：保存媒体前遵守 WebUI 的写入限制；当前 12 小时自动保存上限为 {media_limit}。"
                "媒体备注属于隐藏元数据，应写清来源、保存情景、bot 评价和已知用户评价。"
            )
        else:
            parts.append("媒体模块当前关闭，不得调用 attach_media 或 send_media。")
        if self._impression_updates_enabled(ui_settings):
            impression_prompt = str(getattr(ui_settings, "impression_prompt", "") or "").strip()
            parts.append(
                "人物印象只在有稳定证据时更新；弱情绪、单次玩笑或不确定称呼不得自动建档。"
                f"写入强度：{getattr(ui_settings, 'impression_write_level', 'balanced')}；"
                f"更新策略：{getattr(ui_settings, 'impression_update_strategy', 'evidence_only')}；"
                f"跨群同人策略：{getattr(ui_settings, 'impression_identity_strategy', 'separate')}；"
                f"允许新建人物：{'是' if getattr(ui_settings, 'impression_allow_new_people', False) else '否'}；"
                f"最低确认程度：{getattr(ui_settings, 'impression_min_confidence', 3)}/5。"
                "能确认人物 QQ 号时，调用 write_impression 必须填写隐藏 qq_id，用于跨群同人策略；不能确认时不要编造。"
            )
            if impression_prompt:
                parts.extend(["<人物印象规范>", impression_prompt, "</人物印象规范>"])
        else:
            parts.append("人物印象自动更新当前未启用；不得因为写日记而顺手建档或更新人物印象。")
        if bool(getattr(ui_settings, "enable_memos_module", True)):
            memo_policy = str(getattr(ui_settings, "memos_write_policy", "admin_only") or "admin_only")
            memo_limit = int(getattr(ui_settings, "memos_auto_write_limit_12h", 12) or 0)
            memo_policy_label = {
                "admin_only": "仅管理员可写入",
                "admin_allowed": "管理员手动写入",
                "bot_curated": "允许 bot 自主挑选",
                "review": "允许 bot 写入但标记待复核",
            }.get(memo_policy, memo_policy)
            parts.append(
                "备忘录用于保存更碎片但值得长期检索的信息，例如账号提示、聊天片段、名言、待办和用户明确要求记住的话。"
                "不要把普通寒暄、短暂情绪或未经确认的隐私猜测写入备忘录。"
                f"当前备忘录写入策略：{memo_policy_label}；12 小时 bot 写入上限：{memo_limit}。"
                "涉及账号、密码、密钥、私人联系方式或敏感隐私时，调用 write_memo 必须把 sensitive 设为 true。"
                "私密备忘录只有小窝管理员可以写入和读取；用户有私密信息需要保存时，建议存为私密备忘录，不要写进日记正文。"
            )
            parts.append(
                "回忆具体信息（账号提示、约定、偏好、名言、待办等）时，先用 search_memos 查备忘录；"
                "备忘录没有再用 search_diary 搜日记，确定日期后用 read_diary 读全文。"
            )
        else:
            parts.append("备忘录模块当前关闭，不得调用 write_memo、search_memos、read_memo 或 delete_memo。")
        parts.append("</小窝工具隐藏规范>")
        return "\n".join(parts)

    @filter.on_llm_request()
    async def inject_nest_runtime_policy(self, event: AstrMessageEvent, request):
        prompt = str(getattr(request, "prompt", "") or getattr(event, "message_str", "") or "")
        if not self._should_inject_nest_policy(prompt):
            return
        policy = self._nest_runtime_policy_prompt()
        current = str(getattr(request, "system_prompt", "") or "")
        if policy in current:
            return
        request.system_prompt = (current + "\n\n" + policy).strip()

    def _event_sender_id(self, event) -> str:
        event = self._unwrap_event(event)
        cron_payload = self._cron_payload(event)
        if cron_payload is not None:
            # Same rule as AstrBot's cron manager: a cron run acts as whoever created the job.
            return str(cron_payload.get("sender_id") or "").strip()
        getter = getattr(event, "get_sender_id", None)
        if getter:
            try:
                value = getter()
                return str(value or "").strip()
            except Exception:
                pass
        return str(getattr(event, "sender_id", "") or getattr(event, "user_id", "") or "").strip()

    def _event_origin(self, event) -> str:
        event = self._unwrap_event(event)
        return str(getattr(event, "unified_msg_origin", "") or "").strip()

    def _cron_payload(self, event) -> dict | None:
        getter = getattr(self._unwrap_event(event), "get_extra", None)
        if not callable(getter):
            return None
        try:
            payload = getter("cron_payload")
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def _is_managed_cron_event(self, event) -> bool:
        # AstrBot's own future-task tool always records sender_id and cannot set managed_by,
        # so only jobs this plugin mounted from notebook settings skip the permission checks.
        payload = self._cron_payload(event)
        return bool(payload) and payload.get("managed_by") == PLUGIN_NAME and not payload.get("sender_id")

    def _unwrap_event(self, event):
        current = event
        seen: set[int] = set()
        for _ in range(5):
            if current is None or id(current) in seen:
                break
            seen.add(id(current))
            for attr in ("event", "message_event", "astr_event"):
                inner = getattr(current, attr, None)
                if inner is not None and inner is not current:
                    current = inner
                    break
            else:
                context = getattr(current, "context", None)
                inner = getattr(context, "event", None) if context is not None else None
                if inner is not None and inner is not current:
                    current = inner
                    continue
                nested = getattr(context, "context", None) if context is not None else None
                inner = getattr(nested, "event", None) if nested is not None else None
                if inner is not None and inner is not current:
                    current = inner
                    continue
                return current
            continue
        return current or event

    def _is_nest_admin(self, event) -> bool:
        admin_ids = self._configured_admin_ids()
        if not admin_ids:
            return True
        sender_id = self._event_sender_id(event)
        origin = self._event_origin(event)
        return bool(sender_id and sender_id in admin_ids) or bool(origin and origin in admin_ids)

    def _is_strict_nest_admin(self, event) -> bool:
        # Private memos never fall back to "everyone is admin" when no nest admin is configured.
        if self._configured_admin_ids():
            return self._is_nest_admin(event)
        checker = getattr(self._unwrap_event(event), "is_admin", None)
        try:
            return bool(checker()) if callable(checker) else False
        except Exception:
            return False

    async def _notebook_context_for_event(self, event) -> dict:
        origin = self._event_origin(event)
        notebook = await self.tools.resolve_notebook(origin)
        parts = _origin_parts(origin)
        return {
            "notebook_id": str(notebook.get("id") or notebook.get("notebook_id") or "default"),
            "notebook_name": str(notebook.get("name") or notebook.get("notebook_name") or ""),
            "origin_umo": str(notebook.get("origin_umo") or origin),
            "platform_id": str(notebook.get("platform_id") or parts["platform_id"]),
            "message_type": str(notebook.get("message_type") or parts["message_type"]),
            "session_id": str(notebook.get("session_id") or parts["session_id"]),
        }

    def _non_admin_permissions(self) -> set[str]:
        try:
            if hasattr(self.client, "service_settings"):
                return set(getattr(self.client.service_settings.load(), "non_admin_permissions", []) or [])
        except Exception:
            pass
        return set()

    def _memory_recall_limits(self) -> tuple[int, int]:
        settings = self._ui_settings()
        try:
            return int(settings.search_default_top_k), int(settings.search_snippet_chars)
        except Exception:
            return 5, 180

    async def _guard_permission(self, event, permission: str, action_name: str) -> str:
        if self._is_managed_cron_event(event):
            return ""
        _notebook = await self._notebook_context_for_event(event)
        if self._configured_admin_ids() and not self._is_nest_admin(event):
            if permission in self._non_admin_permissions():
                return ""
            return f"只有小窝管理员可以{action_name}。"
        return ""

    async def _guard_group_write_permission(self, event, action_name: str) -> str:
        permission = {
            "写日记": "diary_write",
            "保存媒体": "media_write",
            "发送媒体": "media_send",
            "写人物印象": "impression_write",
            "删除人物印象": "impression_write",
        }.get(action_name, "")
        return await self._guard_permission(event, permission, action_name) if permission else ""

    def _media_saved_count_last_12h(self) -> int:
        if not hasattr(self.client, "media_service"):
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(hours=12)
        count = 0
        for manifest in self.client.media_service.list_manifests():
            for asset in manifest.get("assets", []):
                saved_at = str(asset.get("saved_at") or "")
                if not saved_at:
                    continue
                try:
                    when = datetime.fromisoformat(saved_at.replace("Z", "+00:00"))
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=timezone.utc)
                    if when >= cutoff:
                        count += 1
                except Exception:
                    continue
        return count

    def _media_policy_denial(self, event, ui_settings) -> str:
        if not bool(getattr(ui_settings, "enable_media_module", True)):
            return self._module_disabled_message("媒体")
        policy = str(getattr(ui_settings, "media_auto_save_policy", "") or "admin_only")
        policy = {"manual": "admin_allowed", "bot_pick": "bot_curated"}.get(policy, policy)
        is_admin = self._is_nest_admin(event)
        has_admin_config = bool(self._configured_admin_ids())
        if policy in {"admin_only", "admin_allowed"} and has_admin_config and not is_admin:
            return "当前媒体保存策略只允许小窝管理员手动保存媒体。"
        if policy in {"bot_curated", "review"}:
            limit = int(getattr(ui_settings, "media_auto_save_limit_12h", 0) or 10)
            if limit > 0 and self._media_saved_count_last_12h() >= limit:
                return f"过去 12 小时媒体保存数量已达到上限 {limit}，本次不再保存。"
        return ""

    def _memo_saved_count_last_12h(self) -> int:
        if hasattr(self.client, "memo_service"):
            return self.client.memo_service.count_saved_since(12, recorder="bot")
        return 0

    def _memo_policy_denial(self, event, ui_settings) -> str:
        if not bool(getattr(ui_settings, "enable_memos_module", True)):
            return self._module_disabled_message("备忘录")
        policy = str(getattr(ui_settings, "memos_write_policy", "admin_only") or "admin_only")
        policy = {"manual": "admin_allowed", "bot_pick": "bot_curated"}.get(policy, policy)
        is_admin = self._is_nest_admin(event)
        has_admin_config = bool(self._configured_admin_ids())
        if policy in {"admin_only", "admin_allowed"} and has_admin_config and not is_admin:
            return "当前备忘录写入策略只允许小窝管理员保存。"
        if not is_admin and policy not in {"bot_curated", "review"}:
            return "当前备忘录策略没有允许 bot 自主写入。"
        if policy in {"bot_curated", "review"} and not is_admin:
            limit = int(getattr(ui_settings, "memos_auto_write_limit_12h", 12) or 0)
            if limit > 0 and self._memo_saved_count_last_12h() >= limit:
                return f"过去 12 小时 bot 备忘录写入数量已达到上限 {limit}，本次不再保存。"
        return ""

    def _module_disabled_message(self, module_name: str) -> str:
        return f"{module_name} 模块当前已在小窝设置中关闭，未执行工具调用。"

    async def _send_image_to_event(self, event: AstrMessageEvent, image_path, caption: str = "") -> None:
        await self._send_image_to_origin(self._event_origin(event), image_path, caption=caption)

    async def _send_image_to_origin(self, origin: str, image_path, caption: str = "") -> None:
        if not origin:
            raise RuntimeError("当前会话不支持主动发送图片。")
        image_value, image_kind = self._normalize_image_payload(image_path)
        max_retries, failure_notice = self._image_send_retry_settings()
        attempts = max_retries + 1
        retry_payload = ""
        last_error = None

        for attempt_index in range(attempts):
            send_value = image_value
            send_kind = image_kind
            if attempt_index > 0:
                if not retry_payload:
                    retry_payload = await asyncio.to_thread(self._make_qq_safe_image_payload, image_value, image_kind)
                if retry_payload:
                    send_value = retry_payload
                    send_kind = "base64"
            chain = self._build_image_message_chain(send_value, send_kind, caption=caption)
            try:
                ok = await self.context.send_message(origin, chain)
                if ok is False:
                    raise RuntimeError("AstrBot 未找到可用会话，图片没有发出。")
                return
            except Exception as exc:
                last_error = exc
                if attempt_index + 1 < attempts:
                    await asyncio.sleep(min(2.0, 0.6 + attempt_index * 0.35))

        if failure_notice:
            try:
                await self._send_text_to_origin(origin, f"图片推送失败，已重试 {max_retries} 次仍未成功。请稍后再试。")
            except Exception:
                pass
        raise RuntimeError(
            f"图片发送失败：QQ/NT 富媒体上传失败。已尝试 {attempts} 次；最后错误：{last_error}"
        ) from last_error

    def _image_send_retry_settings(self) -> tuple[int, bool]:
        try:
            settings = self.client.service_settings.load() if hasattr(self.client, "service_settings") else ServiceUiSettings()
        except Exception:
            settings = ServiceUiSettings()
        try:
            retries = int(getattr(settings, "diary_image_send_max_retries", 3))
        except Exception:
            retries = 3
        retries = max(0, min(retries, 10))
        notice = bool(getattr(settings, "diary_image_send_failure_notice", True))
        return retries, notice

    def _build_image_message_chain(self, image_value: str, image_kind: str, caption: str = "") -> MessageChain:
        chain = MessageChain()
        if caption:
            chain = chain.message(caption)
        if image_kind == "url" and hasattr(chain, "url_image"):
            chain = chain.url_image(image_value)
        elif image_kind == "base64" and hasattr(chain, "base64_image"):
            chain = chain.base64_image(image_value.removeprefix("base64://"))
        elif image_kind == "file" and hasattr(chain, "file_image"):
            chain = chain.file_image(image_value)
        else:
            image_component = self._image_component_for_payload(image_value, image_kind)
            if image_component is None or not hasattr(chain, "chain"):
                raise RuntimeError("当前 AstrBot 版本缺少可用的图片发送接口。")
            chain.chain.append(image_component)
        return chain

    def _normalize_image_payload(self, image_payload) -> tuple[str, str]:
        if isinstance(image_payload, bytes):
            suffix = ".png" if image_payload.startswith(b"\x89PNG") else ".jpg"
            fd, tmp_path = tempfile.mkstemp(prefix="nest_diary_push_", suffix=suffix)
            with os.fdopen(fd, "wb") as f:
                f.write(image_payload)
            return tmp_path, "file"
        value = str(image_payload or "").strip()
        if not value:
            raise FileNotFoundError("图片渲染结果为空，无法发送。")
        if value.startswith(("http://", "https://")):
            return value, "url"
        if value.startswith("base64://"):
            return value, "base64"
        if value.startswith("data:image/") and "," in value:
            return "base64://" + value.split(",", 1)[1], "base64"
        if value.startswith("file:///"):
            value = value[8:]
        path = Path(value)
        if not path.exists():
            raise FileNotFoundError(f"图片文件不存在：{value}")
        return str(path), "file"

    def _image_payload_bytes(self, image_value: str, image_kind: str) -> bytes | None:
        try:
            if image_kind == "file":
                path = Path(image_value[8:] if image_value.startswith("file:///") else image_value)
                if path.exists():
                    return path.read_bytes()
            if image_kind == "base64":
                value = image_value.removeprefix("base64://")
                return base64.b64decode(value)
            if image_value.startswith("data:image/") and "," in image_value:
                return base64.b64decode(image_value.split(",", 1)[1])
        except Exception:
            return None
        return None

    def _make_qq_safe_image_payload(self, image_value: str, image_kind: str) -> str:
        if PILImage is None:
            return ""
        image_bytes = self._image_payload_bytes(image_value, image_kind)
        if not image_bytes:
            return ""
        try:
            image = PILImage.open(io.BytesIO(image_bytes))
            if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                background = PILImage.new("RGB", image.size, (255, 255, 255))
                rgba = image.convert("RGBA")
                background.paste(rgba, mask=rgba.getchannel("A"))
                image = background
            else:
                image = image.convert("RGB")

            max_width = 1280
            if image.width > max_width:
                ratio = max_width / image.width
                image = image.resize((max_width, max(1, int(image.height * ratio))), PILImage.LANCZOS)

            max_pixels = 8_000_000
            pixels = image.width * image.height
            if pixels > max_pixels:
                ratio = (max_pixels / pixels) ** 0.5
                image = image.resize((max(1, int(image.width * ratio)), max(1, int(image.height * ratio))), PILImage.LANCZOS)

            last = b""
            for quality in (88, 78, 68):
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=quality, optimize=True)
                data = buffer.getvalue()
                last = data
                if len(data) <= 3 * 1024 * 1024:
                    break
            return "base64://" + base64.b64encode(last).decode("ascii")
        except Exception:
            return ""

    def _image_component_for_payload(self, image_value: str, image_kind: str):
        for module_name in ("astrbot.api.message_components", "astrbot.core.message.components"):
            try:
                module = __import__(module_name, fromlist=["Image"])
                image = getattr(module, "Image", None)
                if not image:
                    continue
                if image_kind == "url" and hasattr(image, "fromURL"):
                    return image.fromURL(image_value)
                if image_kind == "base64" and hasattr(image, "fromBase64"):
                    return image.fromBase64(image_value.removeprefix("base64://"))
                if image_kind == "file" and hasattr(image, "fromFileSystem"):
                    return image.fromFileSystem(image_value)
            except Exception:
                continue
        return None

    async def _send_text_to_origin(self, origin: str, text: str) -> None:
        if not origin:
            raise RuntimeError("当前会话不支持主动发送消息。")
        await self.context.send_message(origin, MessageChain().message(text))

    def _admin_private_origin(self, event, notebook: dict | None = None) -> str:
        admin_ids = sorted(self._configured_admin_ids())
        if not admin_ids:
            return ""
        current_origin = self._event_origin(event)
        current_parts = _origin_parts(current_origin)
        notebook = notebook or {}

        full_admin_origins = [item for item in admin_ids if _origin_is_full_umo(item)]
        if current_origin in full_admin_origins:
            return current_origin

        platform_id = str(notebook.get("platform_id") or current_parts["platform_id"] or "").strip()
        if not platform_id:
            return ""

        for admin_origin in full_admin_origins:
            parts = _origin_parts(admin_origin)
            if parts["platform_id"] == platform_id and _message_type_family(parts["message_type"]) == "private":
                return admin_origin

        plain_admin_ids = [item for item in admin_ids if not _origin_is_full_umo(item)]
        if not plain_admin_ids:
            return ""
        sender_id = self._event_sender_id(event)
        admin_id = sender_id if sender_id in plain_admin_ids else plain_admin_ids[0]

        if (
            current_parts["platform_id"] == platform_id
            and current_parts["session_id"] == admin_id
            and _message_type_family(current_parts["message_type"]) == "private"
        ):
            return current_origin

        message_type = self._private_message_type_for_platform(platform_id, current_parts, notebook)
        return f"{platform_id}:{message_type}:{admin_id}"

    def _private_message_type_for_platform(self, platform_id: str, current_parts: dict[str, str], notebook: dict) -> str:
        candidates = [
            str(notebook.get("message_type") or ""),
            str(current_parts.get("message_type") or ""),
        ]
        try:
            if hasattr(self.client, "diary_service"):
                for item in self.client.diary_service.notebooks.list_notebooks():
                    if item.platform_id == platform_id:
                        candidates.append(item.message_type)
                        for alias in item.origin_aliases or []:
                            candidates.append(_origin_parts(alias)["message_type"])
        except Exception:
            pass

        for candidate in candidates:
            if _message_type_family(candidate) == "private":
                return candidate
        for candidate in candidates:
            candidate = str(candidate or "").strip()
            if "GroupMessage" in candidate:
                return candidate.replace("GroupMessage", "FriendMessage")
            if candidate.lower() == "group":
                return "private"
        return "private"

    def _diary_push_text(self, entry: dict) -> str:
        title = entry.get("title") or entry.get("date") or "小窝日记"
        notebook_name = entry.get("notebook_name") or "默认日记本"
        body = entry.get("body") or ""
        return f"{entry.get('date', '')} · {notebook_name}\n《{title}》\n\n{body}".strip()

    def _render_template_locally(self, template: str, data: dict) -> str:
        def escape(value) -> str:
            # Braces become entities so the remote T2I renderer never re-evaluates diary text as Jinja.
            return html_escape(str(value)).replace("{", "&#123;").replace("}", "&#125;")

        safe_data = {}
        for key, value in data.items():
            if isinstance(value, str):
                safe_data[key] = escape(value)
            elif isinstance(value, list):
                safe_data[key] = [escape(item) for item in value]
            else:
                safe_data[key] = value
        if SandboxedEnvironment:
            return SandboxedEnvironment().from_string(template).render(**safe_data)
        rendered = template
        for key, value in safe_data.items():
            if isinstance(value, list):
                value = "、".join(value)
            for pattern in (f"{{{{ {key} }}}}", f"{{{{{key}}}}}"):
                rendered = rendered.replace(pattern, str(value))
        return rendered

    def _prepare_diary_push_html(self, template: str, data: dict) -> str:
        html = self._render_template_locally(template, data)
        long_image_css = (
            "<style id=\"nest-diary-push-long-image-fix\">"
            "html,body{margin:0!important;padding:0!important;width:760px!important;"
            "min-width:760px!important;max-width:760px!important;background:transparent!important;"
            "overflow:visible!important;}"
            "body{display:block!important;box-sizing:border-box!important;}"
            ".diary-push-page{width:760px!important;max-width:760px!important;box-sizing:border-box!important;"
            "height:auto!important;min-height:360px!important;overflow:visible!important;}"
            ".diary-push-page *{box-sizing:border-box!important;}"
            "</style>"
        )
        if "</head>" in html:
            return html.replace("</head>", f"{long_image_css}</head>", 1)
        return f"<!doctype html><html><head><meta charset=\"utf-8\">{long_image_css}</head><body>{html}</body></html>"

    def _image_payload_is_valid(self, image_payload) -> bool:
        head = b""
        def decode_head(value: str) -> bytes:
            sample = value[:128]
            sample += "=" * (-len(sample) % 4)
            return base64.b64decode(sample)[:10]

        try:
            if isinstance(image_payload, bytes):
                head = image_payload[:10]
            else:
                value = str(image_payload or "").strip()
                if value.startswith(("http://", "https://")):
                    return True
                if value.startswith("base64://"):
                    head = decode_head(value.removeprefix("base64://"))
                elif value.startswith("data:image/") and "," in value:
                    head = decode_head(value.split(",", 1)[1])
                else:
                    if value.startswith("file:///"):
                        value = value[8:]
                    path = Path(value)
                    if path.exists():
                        with path.open("rb") as f:
                            head = f.read(10)
        except Exception:
            return False
        return head.startswith(b"\xff\xd8") or head.startswith(b"\x89PNG")

    def _crop_diary_render_payload_if_needed(self, image_payload):
        if PILImage is None or ImageChops is None:
            return image_payload

        source_path = None
        image_bytes = None
        value = ""
        if isinstance(image_payload, bytes):
            image_bytes = image_payload
        else:
            value = str(image_payload or "").strip()
            if value.startswith("base64://"):
                image_bytes = base64.b64decode(value.removeprefix("base64://"))
            elif value.startswith("data:image/") and "," in value:
                image_bytes = base64.b64decode(value.split(",", 1)[1])
            elif value.startswith(("http://", "https://")):
                return image_payload
            else:
                if value.startswith("file:///"):
                    value = value[8:]
                source_path = Path(value)
                if not source_path.exists():
                    return image_payload

        try:
            if image_bytes is not None:
                image = PILImage.open(io.BytesIO(image_bytes))
                suffix = ".png" if image_bytes.startswith(b"\x89PNG") else ".jpg"
            else:
                image = PILImage.open(source_path)
                suffix = (source_path.suffix or ".png").lower()

            rgb = image.convert("RGB")
            width, height = rgb.size
            if width <= 0 or height <= 0:
                return image_payload

            background = rgb.getpixel((width - 1, height - 1))
            background_is_blank = all(channel >= 245 for channel in background)
            if not background_is_blank and width <= 820:
                return image_payload

            diff = ImageChops.difference(rgb, PILImage.new("RGB", rgb.size, background))
            bbox = diff.getbbox()
            if not bbox:
                return image_payload

            left, top, right, bottom = bbox
            right_margin = width - right
            bottom_margin = height - bottom
            if right_margin < 24 and bottom_margin < 24:
                return image_payload
            if (right - left) < 320 or (bottom - top) < 180:
                return image_payload

            cropped = image.crop((max(0, left), max(0, top), min(width, right), min(height, bottom)))
            save_suffix = ".png" if suffix not in {".jpg", ".jpeg"} else ".jpg"
            fd, tmp_path = tempfile.mkstemp(prefix="nest_diary_push_cropped_", suffix=save_suffix)
            os.close(fd)
            if save_suffix == ".jpg":
                cropped.convert("RGB").save(tmp_path, format="JPEG", quality=95)
            else:
                cropped.save(tmp_path, format="PNG")
            return tmp_path
        except Exception:
            return image_payload

    async def _render_diary_push_image(self, entry: dict, ui_settings: ServiceUiSettings):
        data = {
            "date": entry.get("date", ""),
            "title": entry.get("title", ""),
            "body": entry.get("body", ""),
            "notebook_name": entry.get("notebook_name", "默认日记本"),
            "mood": entry.get("mood", []),
            "tags": entry.get("tags", []),
            "people": entry.get("people", []),
        }
        template = self._selected_diary_t2i_template(ui_settings)
        html_render = getattr(self, "html_render", None)
        if html_render:
            html = self._prepare_diary_push_html(template, data)
            strategies = [
                {
                    "full_page": True,
                    "type": "png",
                    "animations": "disabled",
                    "caret": "hide",
                    "timeout": 90000,
                    "viewport": {"width": 760, "height": 600},
                    "device_scale_factor_level": "high",
                },
                {
                    "full_page": True,
                    "type": "jpeg",
                    "quality": 92,
                    "animations": "disabled",
                    "caret": "hide",
                    "timeout": 90000,
                    "viewport": {"width": 760, "height": 600},
                    "device_scale_factor_level": "normal",
                },
            ]
            last_error = None
            for options in strategies:
                try:
                    image_payload = await html_render(html, {}, return_url=False, options=options)
                    if self._image_payload_is_valid(image_payload):
                        return await asyncio.to_thread(self._crop_diary_render_payload_if_needed, image_payload)
                    last_error = RuntimeError("T2I 返回的不是有效图片。")
                except Exception as exc:
                    last_error = exc
            if last_error:
                text_to_image = getattr(self, "text_to_image", None)
                if text_to_image:
                    return await text_to_image(self._diary_push_text(entry), return_url=False)
                raise RuntimeError(f"图片推送渲染失败：{last_error}") from last_error
        text_to_image = getattr(self, "text_to_image", None)
        if text_to_image:
            return await text_to_image(self._diary_push_text(entry), return_url=False)
        raise RuntimeError("当前 AstrBot 版本没有可用的文字转图片接口。")

    def _selected_diary_t2i_template(self, ui_settings: ServiceUiSettings) -> str:
        name = str(getattr(ui_settings, "diary_t2i_template_name", "") or "").strip()
        raw = str(getattr(ui_settings, "diary_t2i_template", "") or "").strip()
        builtin_templates = {
            "plain_note": getattr(ServiceUiSettings(), "diary_t2i_template", DEFAULT_DIARY_T2I_TEMPLATE),
            "terminal_report": (
                "<!doctype html><html><head><meta charset=\"utf-8\"><style>"
                "html,body{margin:0;padding:0;width:760px;background:transparent;}"
                "body{font-family:'Microsoft YaHei','Noto Sans SC',sans-serif;color:#1f2527;}"
                ".diary-push-page{box-sizing:border-box;width:760px;min-height:360px;padding:42px 46px 50px;"
                "background:#f1f4f2;border:1px solid #2c3b3b;}"
                ".head{display:flex;justify-content:space-between;gap:18px;border-bottom:3px solid #2c3b3b;"
                "padding-bottom:14px;margin-bottom:24px;font-size:18px;line-height:1.4;font-weight:900;}"
                ".meta{color:#58706b;font-weight:800;text-align:right;}"
                "h1{margin:0 0 22px;font-size:32px;line-height:1.2;font-weight:900;letter-spacing:0;}"
                ".body{white-space:pre-wrap;font-size:19px;line-height:1.76;word-break:break-word;}"
                "</style></head><body><main class=\"diary-push-page\">"
                "<div class=\"head\"><strong>小窝日记</strong><span class=\"meta\">{{ date }} / {{ notebook_name }}</span></div>"
                "<h1>{{ title }}</h1><div class=\"body\">{{ body }}</div>"
                "</main></body></html>"
            ),
            "magazine_page": (
                "<!doctype html><html><head><meta charset=\"utf-8\"><style>"
                "html,body{margin:0;padding:0;width:760px;background:transparent;}"
                "body{font-family:'Microsoft YaHei','Noto Sans SC',sans-serif;color:#202124;}"
                ".diary-push-page{box-sizing:border-box;width:760px;min-height:360px;padding:54px 50px 58px;background:#fbfaf5;}"
                ".rule{width:64px;height:5px;background:#d25f45;margin-bottom:28px;}"
                ".meta{margin:0 0 16px;color:#6a756f;font-size:17px;line-height:1.4;font-weight:800;}"
                "h1{margin:0 0 26px;font-size:38px;line-height:1.16;font-weight:900;letter-spacing:0;}"
                ".body{white-space:pre-wrap;font-size:20px;line-height:1.86;word-break:break-word;}"
                "</style></head><body><main class=\"diary-push-page\">"
                "<div class=\"rule\"></div><p class=\"meta\">{{ date }} · {{ notebook_name }}</p>"
                "<h1>{{ title }}</h1><div class=\"body\">{{ body }}</div>"
                "</main></body></html>"
            ),
        }
        if name in builtin_templates:
            return builtin_templates[name]
        if raw.startswith("{"):
            try:
                data = json.loads(raw)
                for item in data.get("templates", []):
                    if str(item.get("id") or "").strip() == name:
                        template = str(item.get("template") or "").strip()
                        if template:
                            return template
            except Exception:
                pass
        if raw and not raw.startswith("{"):
            return raw
        return getattr(ServiceUiSettings(), "diary_t2i_template", DEFAULT_DIARY_T2I_TEMPLATE)

    async def _read_diary_for_push(
        self,
        date: str,
        selected_notebook: str,
        event,
        notebook: dict,
    ) -> tuple[dict, str]:
        tried: list[str] = []

        async def try_read(notebook_id: str) -> dict | None:
            notebook_id = notebook_id or "default"
            if notebook_id in tried:
                return None
            tried.append(notebook_id)
            try:
                return await self.tools.read_diary(date, notebook_id=notebook_id)
            except FileNotFoundError:
                return None

        for candidate in [selected_notebook or "default", "default"]:
            entry = await try_read(candidate)
            if entry:
                return entry, candidate

        origin_parts = _origin_parts(self._event_origin(event))
        if _message_type_family(origin_parts["message_type"]) == "private" and self._is_nest_admin(event):
            matches: list[tuple[dict, str]] = []
            try:
                notebooks = (await self.tools.list_notebooks()).get("items") or []
            except Exception:
                notebooks = []
            for item in notebooks:
                notebook_id = str(item.get("id") or item.get("notebook_id") or "").strip()
                if not notebook_id or notebook_id in tried:
                    continue
                entry = await try_read(notebook_id)
                if entry:
                    matches.append((entry, notebook_id))
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                names = []
                for entry, notebook_id in matches:
                    names.append(str(entry.get("notebook_name") or notebook_id))
                raise RuntimeError(
                    f"{date} 在多个日记本里都存在：{'、'.join(names)}。请先在 WebUI 选择正确日记本，避免串群推送。"
                )

        notebook_label = notebook.get("notebook_name") or selected_notebook or "默认日记本"
        raise FileNotFoundError(
            f"{date} 在当前日记本“{notebook_label}”中没有找到；已检查默认日记本。"
            "如果这篇日记属于其他群组，请在对应群组推送，或在管理员私聊中处理。"
        )

    async def _push_diary_entry(self, event, date: str, notebook_id: str = "", target: str = "", push_format: str = "") -> str:
        ui_settings = self.client.service_settings.load() if hasattr(self.client, "service_settings") else ServiceUiSettings()
        notebook = await self._notebook_context_for_event(event)
        selected_notebook = notebook_id or notebook["notebook_id"]
        entry, selected_notebook = await self._read_diary_for_push(date, selected_notebook, event, notebook)
        notebook.update(
            {
                "notebook_id": entry.get("notebook_id", selected_notebook),
                "notebook_name": entry.get("notebook_name", notebook.get("notebook_name", "")),
                "origin_umo": entry.get("origin_umo", notebook.get("origin_umo", "")),
                "platform_id": entry.get("platform_id", notebook.get("platform_id", "")),
            }
        )
        notebook_config = {}
        try:
            if hasattr(self.client, "diary_service"):
                notebook_config = self.client.diary_service.notebooks.get(selected_notebook).__dict__
        except Exception:
            notebook_config = {}
        if notebook_config:
            notebook.update(
                {
                    "notebook_id": notebook_config.get("id", notebook.get("notebook_id", selected_notebook)),
                    "notebook_name": notebook_config.get("name", notebook.get("notebook_name", "")),
                    "origin_umo": notebook_config.get("origin_umo") or notebook.get("origin_umo", ""),
                    "platform_id": notebook_config.get("platform_id") or notebook.get("platform_id", ""),
                    "message_type": notebook_config.get("message_type") or notebook.get("message_type", ""),
                    "session_id": notebook_config.get("session_id") or notebook.get("session_id", ""),
                }
            )
        target = target or notebook_config.get("push_target") or getattr(ui_settings, "diary_push_target", "none")
        push_format = push_format or getattr(ui_settings, "diary_push_format", "text")
        if target == "none":
            return "已跳过推送。"
        origins: list[str] = []
        if target in {"source", "both"}:
            origins.append(notebook.get("origin_umo") or self._event_origin(event))
        if target in {"admin_private", "both"}:
            admin_origin = self._admin_private_origin(event, notebook)
            if admin_origin:
                origins.append(admin_origin)
        origins = [item for item in dict.fromkeys(origins) if item]
        if not origins:
            raise RuntimeError("没有可用的推送目标，请先绑定日记本会话或填写小窝管理员。")
        if push_format == "image":
            image_path = await self._render_diary_push_image(entry, ui_settings)
            for origin in origins:
                await self._send_image_to_origin(origin, image_path)
        else:
            text = self._diary_push_text(entry)
            for origin in origins:
                await self._send_text_to_origin(origin, text)
        return f"已推送 {date} 的日记。"

    @filter.llm_tool(name="write_diary")
    async def write_diary_tool(
        self,
        event: AstrMessageEvent,
        date: str,
        title: str,
        body: str,
        mood: str = "",
        tags: str = "",
        people: str = "",
        media_refs: str = "",
        reason: str = "",
        notebook_id: str = "",
    ):
        """写入或更新某一天的日记模块记录。

        Args:
            date(string): 日记日期，格式 YYYY-MM-DD。
            title(string): bot 自拟标题，用一句话概括当天记忆，不要直接使用日期。
            body(string): 日记正文，要包含事件、意义、主观评价、情绪、相关人物和未来线索。
            mood(string): 情绪词，多个用逗号分隔。
            tags(string): 检索标签，多个用逗号分隔。
            people(string): 相关人物，多个用逗号分隔。
            media_refs(string): 图片、语音或附件引用，每行一个，可为空。
            reason(string): 写入原因，例如 nightly_archive、manual_update、memory_review。
            notebook_id(string): 可选日记本 ID；未来任务会指定，普通对话留空使用当前会话日记本。
        """
        if not self._diary_module_enabled():
            return self._module_disabled_message("日记")
        try:
            denial = await self._guard_group_write_permission(event, "写日记")
            if denial:
                return denial
            notebook = await self._notebook_context_for_event(event)
            if notebook_id and hasattr(self.client, "diary_service"):
                try:
                    configured = self.client.diary_service.notebooks.get(notebook_id).__dict__
                    notebook.update(
                        {
                            "notebook_id": configured.get("id", notebook_id),
                            "notebook_name": configured.get("name", notebook.get("notebook_name", "")),
                            "origin_umo": configured.get("origin_umo", notebook.get("origin_umo", "")),
                            "platform_id": configured.get("platform_id", notebook.get("platform_id", "")),
                            "message_type": configured.get("message_type", notebook.get("message_type", "")),
                            "session_id": configured.get("session_id", notebook.get("session_id", "")),
                        }
                    )
                except Exception:
                    notebook["notebook_id"] = notebook_id
            result = await self.tools.write_diary(
                date=date,
                title=title,
                body=body,
                mood=_split_words(mood),
                tags=_split_words(tags),
                people=_split_words(people),
                media_refs=_split_lines(media_refs),
                reason=reason,
                **notebook,
            )
            saved_date = result.get("date", date)
            saved_title = result.get("title", title)
            revision = result.get("revision_id") or result.get("revision")
            suffix = f"，快照号：{revision}" if revision else ""
            message = f"已写入 {saved_date}《{saved_title}》{suffix}。"
            touched = result.get("impressions_touched") or []
            if touched:
                message = f"{message}\n已同步触达人物印象：{'、'.join(touched)}。"
        except Exception as exc:
            message = f"写入日记模块失败：{_brief_error(exc)}"
        return message

    @filter.llm_tool(name="read_diary")
    async def read_diary_tool(self, event: AstrMessageEvent, date: str):
        """读取指定日期的日记。

        Args:
            date(string): 要读取的日期，格式 YYYY-MM-DD。
        """
        if not self._diary_module_enabled():
            return self._module_disabled_message("日记")
        try:
            denial = await self._guard_permission(event, "diary_read", "查看日记")
            if denial:
                return denial
            notebook = await self._notebook_context_for_event(event)
            result = await self.tools.read_diary(date, notebook_id=notebook["notebook_id"])
            content = result.get("body") or result.get("content") or result.get("text") or ""
            title = result.get("title") or date
            message = f"{date}《{title}》：\n{content}" if content else f"{date} 没有找到日记。"
        except Exception as exc:
            message = f"读取日记模块失败：{_brief_error(exc)}"
        return message

    @filter.llm_tool(name="search_diary")
    async def search_diary_tool(self, event: AstrMessageEvent, query: str, top_k: int = 5):
        """按关键词搜索日记模块，避免一次性读取全部日记。回忆具体信息时先用 search_memos 查备忘录，没有再搜日记。

        Args:
            query(string): 搜索关键词、日期、人物、事件或情绪线索。
            top_k(number): 最多返回多少条结果。工具只返回片段摘要，不返回整篇日记。
        """
        if not self._diary_module_enabled():
            return self._module_disabled_message("日记")
        try:
            denial = await self._guard_permission(event, "diary_search", "搜索日记")
            if denial:
                return denial
            default_top_k, snippet_chars = self._memory_recall_limits()
            limit = max(1, min(int(top_k), default_top_k))
            notebook = await self._notebook_context_for_event(event)
            result = await self.tools.search_diary(query, top_k=limit, snippet_chars=snippet_chars, notebook_id=notebook["notebook_id"])
            items = result.get("items") or result.get("results") or []
            if not items:
                message = f"没有搜到和“{query}”相关的日记。"
            else:
                lines = [f"搜到 {len(items)} 条和“{query}”相关的日记："]
                for item in items:
                    item_date = item.get("date", "未知日期")
                    item_title = item.get("title", "")
                    snippet = item.get("snippet") or item.get("summary") or item.get("body") or ""
                    tags = "，".join(item.get("tags") or [])
                    people = "，".join(item.get("people") or [])
                    meta = "；".join(part for part in [f"人物：{people}" if people else "", f"标签：{tags}" if tags else ""] if part)
                    lines.append(f"- {item_date}《{item_title}》：{snippet}" + (f"（{meta}）" if meta else ""))
                message = "\n".join(lines)
        except Exception as exc:
            message = f"搜索日记模块失败：{_brief_error(exc)}"
        return message

    @filter.llm_tool(name="attach_media")
    async def attach_media_tool(
        self,
        event: AstrMessageEvent,
        source_path: str,
        date: str,
        original_name: str = "",
        note: str = "",
    ):
        """把图片、语音或附件归档到指定日期的媒体库。

        Args:
            source_path(string): AstrBot 容器内可访问的文件绝对路径。
            date(string): 归档到哪一天，格式 YYYY-MM-DD。
            original_name(string): 原始文件名，可为空。
            note(string): 隐藏备注，写清保存位置、保存情景、bot 自己评价和已知用户评价。
        """
        ui_settings = self.client.service_settings.load() if hasattr(self.client, "service_settings") else ServiceUiSettings()
        denial = await self._guard_group_write_permission(event, "保存媒体")
        if denial:
            return denial
        policy_denial = self._media_policy_denial(event, ui_settings)
        if policy_denial:
            return policy_denial
        if not ui_settings.enable_media_module:
            return self._module_disabled_message("媒体")
        if not ui_settings.media_allow_bot_import and not self._is_nest_admin(event):
            return "媒体模块没有允许 bot 自动导入图片或附件。"
        try:
            if hasattr(self.client, "media_service"):
                used = len(self.client.media_service.list_by_date(date).get("assets", []))
                if used >= ui_settings.media_max_items_per_day:
                    return f"{date} 的媒体数量已经达到上限，未继续保存。"
            result = await self.tools.attach_media(
                source_path=source_path,
                date=date,
                original_name=original_name or None,
                note=note,
                actor_is_admin=self._is_nest_admin(event),
                autonomous=not self._is_nest_admin(event),
            )
            asset = result.get("asset") or {}
            media_id = asset.get("url") or asset.get("sha256") or asset.get("path") or result.get("path") or ""
            message = f"已把媒体归档到 {date}：{media_id}"
        except Exception as exc:
            message = f"归档媒体失败：{_brief_error(exc)}"
        return message

    @filter.llm_tool(name="send_media")
    async def send_media_tool(
        self,
        event: AstrMessageEvent,
        media_ref: str = "",
        date: str = "",
        original_name: str = "",
    ):
        """把小窝媒体库里的原图直接发送给当前会话，不压缩画质。

        Args:
            media_ref(string): 媒体 URL、sha256 或已知引用。
            date(string): 可选日期，格式 YYYY-MM-DD，用来缩小查找范围。
            original_name(string): 可选原始文件名。
        """
        ui_settings = self.client.service_settings.load() if hasattr(self.client, "service_settings") else ServiceUiSettings()
        if not ui_settings.enable_media_module:
            return self._module_disabled_message("媒体")
        denial = await self._guard_group_write_permission(event, "发送媒体")
        if denial:
            return denial
        try:
            result = await self.tools.resolve_media(media_ref=media_ref, date=date, original_name=original_name)
            asset = result.get("asset") or {}
            path = asset.get("path") or ""
            await self._send_image_to_event(event, path)
            return f"已发送图片：{asset.get('original_name') or asset.get('sha256') or media_ref}"
        except Exception as exc:
            return f"发送图片失败：{_brief_error(exc)}"

    @filter.llm_tool(name="push_diary")
    async def push_diary_tool(
        self,
        event: AstrMessageEvent,
        date: str,
        notebook_id: str = "",
        target: str = "",
        push_format: str = "",
    ):
        """把小窝日记推送到指定位置。

        Args:
            date(string): 要推送的日记日期，格式 YYYY-MM-DD。
            notebook_id(string): 可选日记本 ID；留空使用当前会话日记本。
            target(string): 推送目标，none/source/admin_private/both；留空使用小窝设置。
            push_format(string): 推送格式，text 或 image；留空使用小窝设置。
        """
        if not self._diary_module_enabled():
            return self._module_disabled_message("日记")
        try:
            denial = await self._guard_permission(event, "diary_read", "推送日记")
            if denial:
                return denial
            return await self._push_diary_entry(event, date=date, notebook_id=notebook_id, target=target, push_format=push_format)
        except Exception as exc:
            return f"推送日记失败：{_brief_error(exc)}"

    @filter.llm_tool(name="write_memo")
    async def write_memo_tool(
        self,
        event: AstrMessageEvent,
        content: str,
        title: str = "",
        tags: str = "",
        sensitive: bool = False,
        pinned: bool = False,
        source: str = "",
    ):
        """写入一条小窝备忘录，适合保存账号提示、聊天片段、名言、待办或明确要求记住的话。用户的私密信息建议存为私密备忘录（sensitive=true），私密备忘录只有小窝管理员可以写入和读取。

        Args:
            content(string): 备忘录正文。
            title(string): 可选标题，留空会自动生成。
            tags(string): 标签，多个用逗号分隔。
            sensitive(boolean): 是否为私密备忘录；包含账号、密码、密钥、联系方式或其他隐私时必须为 true。
            pinned(boolean): 是否钉在备忘录顶部。
            source(string): 来源说明，例如 manual、bot_autonomous、quote、chat_excerpt。
        """
        if not self._memos_module_enabled():
            return self._module_disabled_message("备忘录")
        try:
            denial = await self._guard_permission(event, "memo_write", "写备忘录")
            if denial:
                return denial
            if sensitive and not self._is_strict_nest_admin(event):
                return "私密备忘录只有小窝管理员可以写入。"
            ui_settings = self._ui_settings()
            policy_denial = self._memo_policy_denial(event, ui_settings)
            if policy_denial:
                return policy_denial
            notebook = await self._notebook_context_for_event(event)
            result = await self.tools.write_memo(
                content=content,
                title=title,
                tags=_split_words(tags),
                source_chat=notebook.get("notebook_name") or notebook.get("session_id") or notebook.get("origin_umo", ""),
                origin_umo=notebook.get("origin_umo", ""),
                platform_id=notebook.get("platform_id", ""),
                message_type=notebook.get("message_type", ""),
                session_id=notebook.get("session_id", ""),
                recorder="human" if self._is_nest_admin(event) else "bot",
                source=source or ("manual" if self._is_nest_admin(event) else "bot_autonomous"),
                sensitive=bool(sensitive),
                pinned=bool(pinned),
                actor_is_admin=self._is_nest_admin(event),
                autonomous=not self._is_nest_admin(event),
            )
            item = result.get("item") or {}
            return f"已写入备忘录：{item.get('title') or title or item.get('id', '')}（{item.get('id', '')}）。"
        except Exception as exc:
            return f"写入备忘录失败：{_brief_error(exc)}"

    @filter.llm_tool(name="search_memos")
    async def search_memos_tool(self, event: AstrMessageEvent, query: str = "", include_archived: bool = False):
        """搜索小窝备忘录。回忆具体信息（账号提示、约定、偏好、名言、待办等）时优先调用本工具，没有结果再用 search_diary 搜日记。

        Args:
            query(string): 搜索关键词。留空返回最近备忘录摘要。
            include_archived(boolean): 是否包含已归档备忘录。
        """
        if not self._memos_module_enabled():
            return self._module_disabled_message("备忘录")
        try:
            denial = await self._guard_permission(event, "memo_read", "搜索备忘录")
            if denial:
                return denial
            result = await self.tools.search_memos(query=query, include_archived=include_archived)
            items = result.get("items") or []
            if not self._is_strict_nest_admin(event):
                items = [item for item in items if not item.get("sensitive")]
            if not items:
                return f"没有找到和“{query}”相关的备忘录。"
            lines = [f"找到 {len(items)} 条备忘录："]
            for item in items[:10]:
                tags_text = "、".join(item.get("tags") or [])
                content = " ".join(str(item.get("content") or "").split())
                if bool(item.get("sensitive")):
                    content = "私密备忘录，内容需用 read_memo 读取。"
                meta = "；".join(part for part in [item.get("created_at", "")[:10], item.get("source_chat", ""), tags_text] if part)
                lines.append(f"- {item.get('id')}｜{item.get('title') or '无标题'}：{content[:120]}" + (f"（{meta}）" if meta else ""))
            return "\n".join(lines)
        except Exception as exc:
            return f"搜索备忘录失败：{_brief_error(exc)}"

    @filter.llm_tool(name="read_memo")
    async def read_memo_tool(self, event: AstrMessageEvent, memo_id: str):
        """读取指定小窝备忘录。私密备忘录只有小窝管理员可以读取。

        Args:
            memo_id(string): 备忘录 ID。
        """
        if not self._memos_module_enabled():
            return self._module_disabled_message("备忘录")
        try:
            denial = await self._guard_permission(event, "memo_read", "查看备忘录")
            if denial:
                return denial
            item = await self.tools.read_memo(memo_id)
            if item.get("sensitive") and not self._is_strict_nest_admin(event):
                return "这是一条私密备忘录，只有小窝管理员可以读取。"
            tags_text = "、".join(item.get("tags") or [])
            parts = [
                f"{item.get('title') or memo_id}（{item.get('id', memo_id)}）",
                item.get("content", ""),
                f"标签：{tags_text}" if tags_text else "",
                f"来源：{item.get('source_chat') or item.get('origin_umo')}" if item.get("source_chat") or item.get("origin_umo") else "",
                f"记录者：{item.get('recorder', '')}；时间：{item.get('created_at', '')}",
            ]
            return "\n".join(part for part in parts if part)
        except Exception as exc:
            return f"读取备忘录失败：{_brief_error(exc)}"

    @filter.llm_tool(name="delete_memo")
    async def delete_memo_tool(self, event: AstrMessageEvent, memo_id: str):
        """删除指定小窝备忘录。只有确认备忘录明显错误、重复或不再需要时才调用。

        Args:
            memo_id(string): 备忘录 ID。
        """
        if not self._memos_module_enabled():
            return self._module_disabled_message("备忘录")
        try:
            denial = await self._guard_permission(event, "memo_delete", "删除备忘录")
            if denial:
                return denial
            if not self._is_strict_nest_admin(event):
                memo = self.client.memo_service.get(memo_id)
                if memo and getattr(memo, "sensitive", False):
                    return "这是一条私密备忘录，只有小窝管理员可以删除。"
            await self.tools.delete_memo(memo_id)
            return f"已删除备忘录：{memo_id}。"
        except Exception as exc:
            return f"删除备忘录失败：{_brief_error(exc)}"

    @filter.llm_tool(name="list_impressions")
    async def list_impressions_tool(self, event: AstrMessageEvent):
        """列出小窝中已经记录的人物印象摘要。"""
        if not self._impressions_module_enabled():
            return self._module_disabled_message("人物印象")
        try:
            denial = await self._guard_permission(event, "impression_read", "查看人物印象")
            if denial:
                return denial
            result = await self.tools.list_impressions()
            items = result.get("items") or []
            if not items:
                message = "还没有记录任何人物印象。"
            else:
                lines = [f"已有 {len(items)} 条人物印象："]
                for item in items:
                    lines.append(f"- {item.get('name', '未知')}：{item.get('summary', '')}")
                message = "\n".join(lines)
        except Exception as exc:
            message = f"读取人物印象列表失败：{_brief_error(exc)}"
        return message

    @filter.llm_tool(name="read_impression")
    async def read_impression_tool(self, event: AstrMessageEvent, name: str):
        """读取指定人物的长期印象。

        Args:
            name(string): 人物名。
        """
        if not self._impressions_module_enabled():
            return self._module_disabled_message("人物印象")
        try:
            denial = await self._guard_permission(event, "impression_read", "查看人物印象")
            if denial:
                return denial
            item = await self.tools.read_impression(name)
            parts = [f"{item.get('name', name)} 的人物印象：", item.get("summary", "")]
            if item.get("identity"):
                parts.append("身份：" + item["identity"])
            if item.get("traits"):
                parts.append("性格：" + "，".join(item["traits"]))
            if item.get("hobbies"):
                parts.append("爱好：" + "，".join(item["hobbies"]))
            if item.get("interests"):
                parts.append("兴趣：" + "，".join(item["interests"]))
            if item.get("preferences"):
                parts.append("偏好：" + "，".join(item["preferences"]))
            if item.get("relationship"):
                parts.append("关系：" + item["relationship"])
            if item.get("affinity"):
                parts.append(f"喜爱程度：{item['affinity']}/5")
            if item.get("special_comment"):
                parts.append("特殊点评：" + item["special_comment"])
            if item.get("evidence_dates"):
                parts.append("证据日期：" + "，".join(item["evidence_dates"]))
            if item.get("notes"):
                parts.append("备注：" + item["notes"])
            message = "\n".join(part for part in parts if part)
        except Exception as exc:
            message = f"读取人物印象失败：{_brief_error(exc)}"
        return message

    @filter.llm_tool(name="write_impression")
    async def write_impression_tool(
        self,
        event: AstrMessageEvent,
        name: str,
        summary: str,
        identity: str = "",
        traits: str = "",
        hobbies: str = "",
        interests: str = "",
        preferences: str = "",
        relationship: str = "",
        affinity: int = 3,
        special_comment: str = "",
        evidence_dates: str = "",
        confidence: int = 3,
        notes: str = "",
        qq_id: str = "",
    ):
        """写入或更新指定人物的长期印象。

        Args:
            name(string): 人物名。
            summary(string): 对这个人的稳定总结，必须基于可追溯证据。
            identity(string): 身份、关系定位或长期角色。
            traits(string): 性格特征，多个用逗号分隔。
            hobbies(string): 爱好，多个用逗号分隔。
            interests(string): 兴趣爱好，多个用逗号分隔。
            preferences(string): 偏好或相处方式，多个用逗号分隔。
            relationship(string): 与 bot 或项目的关系。
            affinity(number): 喜爱程度，1 到 5。
            special_comment(string): bot 根据人设写出的主观特殊点评。
            evidence_dates(string): 支撑这次更新的日记日期，多个用逗号分隔。
            confidence(number): 可信度，1 到 5。
            notes(string): 额外备注。
            qq_id(string): 隐藏 QQ 号标签；能确认时必须填写，用于跨群人物收束。
        """
        if not self._impressions_module_enabled():
            return self._module_disabled_message("人物印象")
        try:
            denial = await self._guard_group_write_permission(event, "写人物印象")
            if denial:
                return denial
            notebook = await self._notebook_context_for_event(event)
            result = await self.tools.write_impression(
                name=name,
                summary=summary,
                qq_id=qq_id,
                source_chat=notebook.get("notebook_name") or notebook.get("session_id") or notebook.get("origin_umo", ""),
                identity=identity,
                traits=_split_words(traits),
                hobbies=_split_words(hobbies),
                interests=_split_words(interests),
                preferences=_split_words(preferences),
                relationship=relationship,
                affinity=int(affinity),
                special_comment=special_comment,
                evidence_dates=_split_words(evidence_dates),
                confidence=int(confidence),
                notes=notes,
            )
            item = result.get("item") or {}
            message = f"已更新 {item.get('name', name)} 的人物印象。"
        except Exception as exc:
            message = f"写入人物印象失败：{_brief_error(exc)}"
        return message

    @filter.llm_tool(name="delete_impression")
    async def delete_impression_tool(self, event: AstrMessageEvent, name: str):
        """删除指定人物印象。只有确认这条人物印象明显错误、重复或不再需要时才调用。

        Args:
            name(string): 人物名。
        """
        if not self._impressions_module_enabled():
            return self._module_disabled_message("人物印象")
        try:
            denial = await self._guard_group_write_permission(event, "删除人物印象")
            if denial:
                return denial
            await self.tools.delete_impression(name)
            message = f"已删除 {name} 的人物印象。"
        except Exception as exc:
            message = f"删除人物印象失败：{_brief_error(exc)}"
        return message

    def _impressions_module_enabled(self) -> bool:
        try:
            if hasattr(self.client, "service_settings"):
                return bool(self.client.service_settings.load().enable_impressions_module)
        except Exception:
            pass
        return True
