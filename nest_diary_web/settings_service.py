from __future__ import annotations

import copy
import json
import secrets
from dataclasses import asdict

from nest_diary_web.models import SecuritySettings, ServiceUiSettings
from nest_diary_web.paths import NestPaths, atomic_write_text


class ServiceSettingsStore:
    def __init__(self, paths: NestPaths):
        self.paths = paths
        self.paths.ensure_all()
        self.path = self.paths.settings_dir / "service-ui.json"
        self._cache: tuple[tuple[int, int, int], ServiceUiSettings] | None = None

    def load(self) -> ServiceUiSettings:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return self._normalize(ServiceUiSettings())
        # Saves replace the file atomically, so st_ino changes even when coarse Windows mtimes do not.
        key = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
        if self._cache is None or self._cache[0] != key:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            defaults = asdict(ServiceUiSettings())
            defaults.update(data)
            self._cache = (key, self._normalize(ServiceUiSettings(**defaults)))
        # Callers mutate and re-save what they get, so never hand out the cached instance itself.
        return copy.deepcopy(self._cache[1])

    def save(self, settings: ServiceUiSettings) -> ServiceUiSettings:
        settings = self._normalize(settings)
        atomic_write_text(self.path, json.dumps(asdict(settings), ensure_ascii=False, indent=2))
        self._cache = None
        return settings

    def _normalize(self, settings: ServiceUiSettings) -> ServiceUiSettings:
        settings.site_title = (settings.site_title or "").strip() or "小窝"
        settings.site_subtitle = (settings.site_subtitle or "").strip() or "把今天安放好，旧事也能被轻轻找回来"
        settings.brand_avatar_url = (settings.brand_avatar_url or "").strip()
        settings.enable_diary_module = bool(settings.enable_diary_module)
        settings.search_default_top_k = max(1, min(int(settings.search_default_top_k), 20))
        settings.search_snippet_chars = max(80, min(int(settings.search_snippet_chars), 360))
        settings.memory_recall_enabled = bool(settings.memory_recall_enabled)
        if settings.memory_recall_policy not in {"conservative", "active"}:
            settings.memory_recall_policy = "conservative"
        if settings.diary_archive_granularity not in {"day", "month", "year"}:
            settings.diary_archive_granularity = "day"
        if settings.diary_display_mode not in {"grouped", "merged"}:
            settings.diary_display_mode = "grouped"
        settings.admin_private_diary_enabled = bool(settings.admin_private_diary_enabled)
        settings.admin_private_push_enabled = bool(settings.admin_private_push_enabled)
        if settings.diary_push_format not in {"text", "image"}:
            settings.diary_push_format = "text"
        if settings.diary_push_target not in {"none", "source", "admin_private", "both"}:
            settings.diary_push_target = "none"
        settings.diary_t2i_template_name = str(settings.diary_t2i_template_name or "plain_note").strip() or "plain_note"
        settings.diary_image_send_max_retries = max(0, min(int(settings.diary_image_send_max_retries), 10))
        settings.diary_image_send_failure_notice = bool(settings.diary_image_send_failure_notice)
        settings.permissions_allow_admin_natural_language = bool(settings.permissions_allow_admin_natural_language)
        allowed_permissions = {
            "diary_read",
            "diary_search",
            "diary_write",
            "diary_delete",
            "media_read",
            "media_write",
            "media_send",
            "impression_read",
            "impression_write",
            "memo_read",
            "memo_write",
            "memo_delete",
        }
        if not isinstance(settings.non_admin_permissions, list):
            settings.non_admin_permissions = []
        settings.non_admin_permissions = [
            item for item in dict.fromkeys(str(item).strip() for item in settings.non_admin_permissions)
            if item in allowed_permissions
        ]
        settings.nest_admin_ids = "\n".join(
            dict.fromkeys(
                item.strip()
                for item in str(settings.nest_admin_ids or "").replace(",", "\n").replace("，", "\n").splitlines()
                if item.strip()
            )
        )
        settings.diary_write_prompt = str(settings.diary_write_prompt or "").strip() or ServiceUiSettings().diary_write_prompt
        settings.diary_t2i_template = str(settings.diary_t2i_template or "").strip() or ServiceUiSettings().diary_t2i_template
        settings.enable_media_module = bool(settings.enable_media_module)
        settings.allow_media_refs = bool(settings.allow_media_refs)
        settings.media_max_items_per_day = max(1, min(int(settings.media_max_items_per_day), 500))
        settings.media_auto_save_policy = {"manual": "admin_allowed", "bot_pick": "bot_curated"}.get(
            settings.media_auto_save_policy,
            settings.media_auto_save_policy,
        )
        if settings.media_auto_save_policy not in {"admin_only", "admin_allowed", "bot_curated", "review"}:
            settings.media_auto_save_policy = "admin_only"
        settings.media_auto_save_limit_12h = max(0, min(int(settings.media_auto_save_limit_12h), 200))
        if settings.media_auto_album_strategy not in {"off", "existing_only", "confirm", "auto"}:
            settings.media_auto_album_strategy = "confirm"
        settings.media_allow_bot_import = bool(settings.media_allow_bot_import)
        settings.media_auto_album = bool(settings.media_auto_album)
        if settings.media_auto_album_strategy == "off":
            settings.media_auto_album = False
        if settings.media_storage_strategy not in {"copy", "move", "cut"}:
            settings.media_storage_strategy = "copy"
        if settings.media_storage_strategy == "cut":
            settings.media_storage_strategy = "move"
        settings.enable_impressions_module = bool(settings.enable_impressions_module)
        settings.auto_impression_from_diary = bool(settings.auto_impression_from_diary)
        if settings.impression_write_level not in {"off", "light", "balanced", "deep"}:
            settings.impression_write_level = "balanced"
        if settings.impression_update_strategy not in {"manual", "evidence_only", "existing_only", "aggressive"}:
            settings.impression_update_strategy = "evidence_only"
        settings.impression_identity_strategy = str(getattr(settings, "impression_identity_strategy", "separate") or "separate")
        if settings.impression_identity_strategy not in {"unified", "nested", "separate"}:
            settings.impression_identity_strategy = "separate"
        settings.impression_allow_new_people = bool(settings.impression_allow_new_people)
        settings.impression_min_confidence = max(1, min(int(settings.impression_min_confidence), 5))
        if settings.impression_write_level == "off":
            settings.auto_impression_from_diary = False
        if settings.impression_update_strategy == "manual":
            settings.impression_allow_new_people = False
        settings.show_impression_prompt = bool(settings.show_impression_prompt)
        settings.enable_memos_module = bool(getattr(settings, "enable_memos_module", True))
        settings.memos_write_policy = {"manual": "admin_allowed", "bot_pick": "bot_curated"}.get(
            getattr(settings, "memos_write_policy", "admin_only"),
            getattr(settings, "memos_write_policy", "admin_only"),
        )
        if settings.memos_write_policy not in {"admin_only", "admin_allowed", "bot_curated", "review"}:
            settings.memos_write_policy = "admin_only"
        settings.memos_auto_write_limit_12h = max(0, min(int(getattr(settings, "memos_auto_write_limit_12h", 12)), 200))
        settings.memos_sensitive_default_hidden = bool(getattr(settings, "memos_sensitive_default_hidden", True))
        settings.active_frontend_style = (settings.active_frontend_style or "").strip() or "default"
        if not isinstance(settings.enabled_official_modules, list):
            settings.enabled_official_modules = ["diary", "impressions", "media", "memos", "webui"]
        if not isinstance(settings.enabled_custom_modules, list):
            settings.enabled_custom_modules = []
        if not isinstance(settings.enabled_custom_extensions, list):
            settings.enabled_custom_extensions = []
        if not isinstance(settings.enabled_appearance_modules, list):
            settings.enabled_appearance_modules = []
        settings.appearance_modules_initialized = bool(settings.appearance_modules_initialized)
        settings.onboarding_completed = bool(getattr(settings, "onboarding_completed", False))
        if not settings.appearance_modules_initialized:
            settings.appearance_modules_initialized = True
        settings.enabled_official_modules = [
            item for item in settings.enabled_official_modules if item in {"diary", "impressions", "media", "memos", "webui"}
        ]
        settings.enabled_official_modules = [
            item
            for item in settings.enabled_official_modules
            if (
                item not in {"diary", "impressions", "media", "memos"}
                or (item == "diary" and settings.enable_diary_module)
                or (item == "impressions" and settings.enable_impressions_module)
                or (item == "media" and settings.enable_media_module)
                or (item == "memos" and settings.enable_memos_module)
            )
        ]
        if settings.enable_diary_module and "diary" not in settings.enabled_official_modules:
            settings.enabled_official_modules.append("diary")
        if settings.enable_impressions_module and "impressions" not in settings.enabled_official_modules:
            settings.enabled_official_modules.append("impressions")
        if settings.enable_media_module and "media" not in settings.enabled_official_modules:
            settings.enabled_official_modules.append("media")
        if settings.enable_memos_module and "memos" not in settings.enabled_official_modules:
            settings.enabled_official_modules.append("memos")
        if not settings.enable_media_module:
            settings.allow_media_refs = False
            settings.media_allow_bot_import = False
            settings.media_auto_album = False
        if not settings.enable_memos_module:
            settings.memos_write_policy = "admin_only"
            settings.memos_auto_write_limit_12h = 0
        settings.enabled_custom_modules = [
            item.strip() for item in settings.enabled_custom_modules if self._safe_package_id(item.strip())
        ]
        settings.enabled_custom_extensions = [
            item.strip() for item in settings.enabled_custom_extensions if self._safe_package_id(item.strip())
        ]
        settings.enabled_appearance_modules = [
            item.strip()
            for item in settings.enabled_appearance_modules
            if self._safe_package_id(item.strip()) and item.strip() != "nest-tactical"
        ]
        if settings.active_frontend_style == "nest-tactical":
            settings.active_frontend_style = "default"
        if not isinstance(getattr(settings, "hidden_module_nav_ids", None), list):
            settings.hidden_module_nav_ids = []
        settings.hidden_module_nav_ids = [
            item.strip() for item in settings.hidden_module_nav_ids if self._safe_package_id(item.strip())
        ]
        settings.custom_webui_dir = (settings.custom_webui_dir or "").strip()
        settings.backup_custom_before_update = bool(settings.backup_custom_before_update)
        legacy_impression_prompt = (
            "写完日记后，请依据你的角色设定和当天日记内容判断："
            "这篇日记是否提供了关于某个人的稳定新证据。"
            "如果有，请先读取旧人物印象，再按变化更新 name、identity、summary、traits、hobbies、interests、preferences、relationship、affinity、special_comment、evidence_dates、confidence、notes；"
            "summary 写稳定总结，special_comment 写带有主观判断的特殊点评。"
            "如果没有稳定变化，不要硬写。"
        )
        current_impression_prompt = str(settings.impression_prompt or "").strip()
        if not current_impression_prompt or current_impression_prompt == legacy_impression_prompt:
            settings.impression_prompt = ServiceUiSettings().impression_prompt
        else:
            settings.impression_prompt = current_impression_prompt
        return settings

    def _safe_package_id(self, value: str) -> bool:
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
        return bool(value) and all(char in allowed for char in value)


class SecuritySettingsStore:
    def __init__(self, paths: NestPaths, default_admin_password: str = "12345678", default_bot_api_token: str = ""):
        self.paths = paths
        self.paths.ensure_all()
        self.path = self.paths.settings_dir / "security.json"
        self.default_admin_password = default_admin_password or "12345678"
        self.default_bot_api_token = default_bot_api_token

    def load(self) -> SecuritySettings:
        defaults = {
            "admin_password": self.default_admin_password,
            "bot_api_token": self.default_bot_api_token,
            "external_api_enabled": bool(self.default_bot_api_token),
        }
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            defaults.update(data)
        return SecuritySettings(**defaults)

    def save(self, settings: SecuritySettings) -> SecuritySettings:
        settings.admin_password = settings.admin_password or self.default_admin_password
        atomic_write_text(self.path, json.dumps(asdict(settings), ensure_ascii=False, indent=2))
        return settings

    def update(
        self,
        admin_password: str | None = None,
        bot_api_token: str | None = None,
        external_api_enabled: bool | None = None,
    ) -> SecuritySettings:
        current = self.load()
        if admin_password:
            current.admin_password = admin_password
        if bot_api_token is not None:
            current.bot_api_token = bot_api_token
        if external_api_enabled is not None:
            current.external_api_enabled = external_api_enabled
        return self.save(current)

    def generate_token(self) -> str:
        return secrets.token_urlsafe(32)
