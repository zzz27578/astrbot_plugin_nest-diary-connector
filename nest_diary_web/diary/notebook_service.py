from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from nest_diary_web.paths import NestPaths, safe_package_id, atomic_write_text


@dataclass
class DiaryNotebook:
    id: str
    name: str
    origin_umo: str = ""
    origin_aliases: list[str] | None = None
    platform_id: str = ""
    message_type: str = ""
    session_id: str = ""
    protocol_audit_tag: str = ""
    enabled: bool = True
    auto_archive_enabled: bool = False
    archive_time: str = "03:00"
    push_enabled: bool = False
    push_target: str = "none"
    push_format: str = "text"
    admins: list[str] | None = None
    created_at: str = ""
    updated_at: str = ""


def _origin_parts(origin_umo: str) -> tuple[str, str, str] | None:
    parts = (origin_umo or "").split(":", 2)
    if len(parts) != 3:
        return None
    return parts[0].strip(), parts[1].strip(), parts[2].strip()


def _message_type_family(message_type: str) -> str:
    normalized = str(message_type or "").strip()
    compact = normalized.lower().replace("_", "").replace("-", "")
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


def _semantic_origin_key(platform_id: str, message_type: str, session_id: str) -> tuple[str, str, str] | None:
    platform_id = str(platform_id or "").strip()
    session_id = str(session_id or "").strip()
    if not platform_id or not session_id:
        return None
    return platform_id, _message_type_family(message_type), session_id


def _semantic_origin_key_from_umo(origin_umo: str) -> tuple[str, str, str] | None:
    parts = _origin_parts(origin_umo)
    if not parts:
        return None
    return _semantic_origin_key(*parts)


def _protocol_audit_tag(platform_id: str, message_type: str, session_id: str) -> str:
    key = _semantic_origin_key(platform_id, message_type, session_id)
    if not key:
        return ""
    raw_type = str(message_type or "").strip()
    return f"umo:v1:{key[0]}:{key[1]}:{key[2]}:{raw_type}"


def _canonical_origin_umo(platform_id: str, message_type: str, session_id: str) -> str:
    if not platform_id or not message_type or not session_id:
        return ""
    return f"{platform_id}:{message_type}:{session_id}"


class NotebookService:
    def __init__(self, paths: NestPaths):
        self.paths = paths
        self.paths.ensure_all()
        self.path = self.paths.diary_notebook_registry_file

    def list_notebooks(self) -> list[DiaryNotebook]:
        items = self._load()
        if "default" not in items:
            items["default"] = self._default_notebook()
            self._save(items)
        return sorted(items.values(), key=lambda item: (item.id != "default", item.name, item.id))

    def get(self, notebook_id: str = "default") -> DiaryNotebook:
        notebook_id = safe_package_id(notebook_id)
        items = self._load()
        if notebook_id not in items:
            if notebook_id == "default":
                items[notebook_id] = self._default_notebook()
                self._save(items)
            else:
                raise KeyError(notebook_id)
        return items[notebook_id]

    def audit_protocols(self) -> dict[str, int]:
        items = self._load()
        updated = 0
        for item in items.values():
            if self._backfill_protocol_fields(item):
                updated += 1
        if updated:
            self._save(items)
        return {"checked": len(items), "updated": updated}

    def bind_origin(self, notebook_id: str, origin_umo: str) -> DiaryNotebook:
        notebook_id = safe_package_id(notebook_id)
        origin_umo = str(origin_umo or "").strip()
        if not notebook_id:
            raise ValueError("notebook_id is required")
        parts = _origin_parts(origin_umo)
        if not parts:
            raise ValueError("origin_umo is invalid")
        platform_id, message_type, session_id = parts
        semantic_key = _semantic_origin_key(platform_id, message_type, session_id)
        items = self._load()
        if notebook_id not in items:
            raise KeyError(notebook_id)
        target = items[notebook_id]
        for item_id, item in items.items():
            if item_id == notebook_id:
                continue
            aliases = []
            for alias in item.origin_aliases or []:
                if alias == origin_umo:
                    continue
                if semantic_key and _semantic_origin_key_from_umo(alias) == semantic_key:
                    continue
                aliases.append(alias)
            item.origin_aliases = aliases
            if item.origin_umo == origin_umo or (semantic_key and _semantic_origin_key_from_umo(item.origin_umo) == semantic_key):
                item.origin_umo = ""
                item.platform_id = ""
                item.message_type = ""
                item.session_id = ""
                item.protocol_audit_tag = ""
                item.updated_at = self._now()
        self._remember_origin_alias(target, target.origin_umo)
        self._remember_origin_alias(target, origin_umo)
        target.origin_umo = origin_umo
        target.platform_id = platform_id
        target.message_type = message_type
        target.session_id = session_id
        self._backfill_protocol_fields(target)
        target.updated_at = self._now()
        items[notebook_id] = target
        self._save(items)
        return target

    def ensure(
        self,
        notebook_id: str = "default",
        name: str = "",
        origin_umo: str = "",
        message_type: str = "",
        platform_id: str = "",
        session_id: str = "",
    ) -> DiaryNotebook:
        notebook_id = safe_package_id(notebook_id)
        items = self._load()
        now = self._now()
        current = items.get(notebook_id)
        if current is None:
            current = DiaryNotebook(
                id=notebook_id,
                name=name or self._name_from_origin(origin_umo, notebook_id),
                origin_umo=origin_umo,
                platform_id=platform_id,
                message_type=message_type,
                session_id=session_id,
                admins=[],
                created_at=now,
                updated_at=now,
            )
        else:
            if name:
                current.name = name
            if origin_umo:
                current.origin_umo = origin_umo
            if platform_id:
                current.platform_id = platform_id
            if message_type:
                current.message_type = message_type
            if session_id:
                current.session_id = session_id
            current.updated_at = now
        self._backfill_protocol_fields(current)
        items[notebook_id] = current
        self._save(items)
        return current

    def save_notebooks(
        self,
        notebooks: list[dict],
        delete_ids: list[str] | None = None,
        replace: bool = False,
    ) -> list[DiaryNotebook]:
        items = self._load()
        now = self._now()
        submitted_ids = {
            safe_package_id(str(raw.get("id") or raw.get("notebook_id") or ""))
            for raw in notebooks
            if str(raw.get("id") or raw.get("notebook_id") or "").strip()
        }
        delete_id_set = {
            safe_package_id(str(raw_id or ""))
            for raw_id in (delete_ids or [])
            if str(raw_id or "").strip()
        }
        if replace:
            delete_id_set.update(
                notebook_id
                for notebook_id in items
                if notebook_id != "default" and notebook_id not in submitted_ids
            )
        for notebook_id in delete_id_set:
            if not notebook_id or notebook_id == "default":
                continue
            items.pop(notebook_id, None)
            notebook_dir = self.paths.diary_notebooks_dir / notebook_id
            if notebook_dir.exists():
                shutil.rmtree(notebook_dir)
        for raw in notebooks:
            raw_id = str(raw.get("id") or raw.get("notebook_id") or "").strip()
            if not raw_id:
                continue
            notebook_id = safe_package_id(raw_id)
            if not notebook_id:
                continue
            if notebook_id in delete_id_set:
                continue
            current = items.get(notebook_id) or DiaryNotebook(
                id=notebook_id,
                name=str(raw.get("name") or notebook_id),
                created_at=now,
                updated_at=now,
                admins=[],
            )
            current.name = str(raw.get("name") or current.name or notebook_id).strip() or notebook_id
            raw_origin = str(raw.get("origin_umo", current.origin_umo or "") or "").strip()
            raw_message_type = str(raw.get("message_type", current.message_type or "") or "").strip()
            raw_session_id = str(raw.get("session_id", current.session_id or "") or "").strip()
            raw_platform_id = str(raw.get("platform_id", current.platform_id or "") or "").strip()
            if (
                current.origin_umo
                and raw_origin
                and raw_platform_id == current.platform_id
                and current.session_id == raw_session_id
                and raw_message_type in {"private", "group"}
                and current.message_type not in {"private", "group"}
            ):
                if (
                    _message_type_family(current.message_type) == _message_type_family(raw_message_type)
                    and raw_origin not in (current.origin_aliases or [])
                ):
                    current.origin_aliases = [*(current.origin_aliases or []), raw_origin]
                raw_origin = current.origin_umo
                raw_message_type = current.message_type
                raw_platform_id = current.platform_id
            current.platform_id = raw_platform_id or current.platform_id
            current.message_type = raw_message_type or current.message_type
            current.session_id = raw_session_id or current.session_id
            current.origin_umo = raw_origin or _canonical_origin_umo(current.platform_id, current.message_type, current.session_id)
            self._backfill_protocol_fields(current)
            current.enabled = bool(raw.get("enabled", current.enabled))
            current.auto_archive_enabled = bool(raw.get("auto_archive_enabled", False))
            current.archive_time = str(raw.get("archive_time") or current.archive_time or "03:00").strip()
            current.push_enabled = bool(raw.get("push_enabled", current.push_enabled))
            current.push_target = str(raw.get("push_target") or current.push_target or "none").strip()
            if current.push_target not in {"none", "source", "admin_private", "both"}:
                current.push_target = "none"
            current.push_format = str(raw.get("push_format") or current.push_format or "text").strip()
            admins = raw.get("admins")
            if isinstance(admins, list):
                current.admins = [str(item).strip() for item in admins if str(item).strip()]
            current.updated_at = now
            items[notebook_id] = current
        if "default" not in items:
            items["default"] = self._default_notebook()
        self._save(items)
        return self.list_notebooks()

    def resolve_from_origin(self, origin_umo: str, default: str = "default") -> DiaryNotebook:
        if not origin_umo:
            return self.get(default)
        items = self._load()
        for item in items.values():
            if item.origin_umo == origin_umo:
                if self._backfill_protocol_fields(item, origin_umo):
                    items[item.id] = item
                    self._save(items)
                return item

        compatible = self._find_protocol_compatible(items, origin_umo)
        if compatible:
            return compatible

        parts = _origin_parts(origin_umo) or ("", "", "")
        platform_id, message_type, session_id = parts
        family = _message_type_family(message_type)
        prefix = "group" if family == "group" else "private" if family == "private" else "session"
        notebook_id = safe_package_id(f"{prefix}_{platform_id}_{session_id}")
        return self.ensure(
            notebook_id=notebook_id,
            origin_umo=origin_umo,
            platform_id=platform_id,
            message_type=message_type,
            session_id=session_id,
        )

    def _default_notebook(self) -> DiaryNotebook:
        now = self._now()
        return DiaryNotebook(id="default", name="默认日记本", admins=[], created_at=now, updated_at=now)

    def _load(self) -> dict[str, DiaryNotebook]:
        if not self.path.exists():
            return {"default": self._default_notebook()}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        raw_items = data.get("items", data if isinstance(data, dict) else [])
        items: dict[str, DiaryNotebook] = {}
        iterable = raw_items.values() if isinstance(raw_items, dict) else raw_items
        allowed_keys = set(DiaryNotebook.__dataclass_fields__.keys())
        for raw in iterable:
            if not isinstance(raw, dict):
                continue
            notebook_id = safe_package_id(str(raw.get("id") or ""))
            if not notebook_id:
                continue
            values = asdict(self._default_notebook())
            values.update({key: value for key, value in raw.items() if key in allowed_keys})
            values["id"] = notebook_id
            values["admins"] = values.get("admins") or []
            values["origin_aliases"] = values.get("origin_aliases") or []
            items[notebook_id] = DiaryNotebook(**values)
        return items

    def _save(self, items: dict[str, DiaryNotebook]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"items": [asdict(item) for item in sorted(items.values(), key=lambda item: item.id)]}
        atomic_write_text(self.path, json.dumps(payload, ensure_ascii=False, indent=2))

    def _name_from_origin(self, origin_umo: str, fallback: str) -> str:
        if not origin_umo:
            return fallback
        parts = _origin_parts(origin_umo)
        if parts:
            family = _message_type_family(parts[1])
            label = "群组" if family == "group" else "私聊" if family == "private" else "会话"
            return f"{label} {parts[2]}"
        return fallback

    def _now(self) -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def _find_protocol_compatible(self, items: dict[str, DiaryNotebook], origin_umo: str) -> DiaryNotebook | None:
        parts = _origin_parts(origin_umo)
        if not parts:
            return None
        platform_id, message_type, session_id = parts
        semantic_key = _semantic_origin_key(platform_id, message_type, session_id)
        if not semantic_key:
            return None
        matches = [
            item
            for item in items.values()
            if self._notebook_semantic_keys(item) & {semantic_key}
        ]
        if len(matches) != 1:
            return None
        item = matches[0]
        self._remember_origin_alias(item, item.origin_umo)
        item.origin_umo = origin_umo
        item.platform_id = platform_id
        item.message_type = message_type
        item.session_id = session_id
        self._backfill_protocol_fields(item)
        item.updated_at = self._now()
        items[item.id] = item
        self._save(items)
        return item

    def _notebook_semantic_keys(self, item: DiaryNotebook) -> set[tuple[str, str, str]]:
        keys: set[tuple[str, str, str]] = set()
        direct_key = _semantic_origin_key(item.platform_id, item.message_type, item.session_id)
        if direct_key:
            keys.add(direct_key)
        origin_key = _semantic_origin_key_from_umo(item.origin_umo)
        if origin_key:
            keys.add(origin_key)
        for alias in item.origin_aliases or []:
            alias_key = _semantic_origin_key_from_umo(alias)
            if alias_key:
                keys.add(alias_key)
        return keys

    def _remember_origin_alias(self, item: DiaryNotebook, origin_umo: str) -> None:
        origin_umo = str(origin_umo or "").strip()
        if not origin_umo:
            return
        aliases = list(item.origin_aliases or [])
        if origin_umo not in aliases:
            aliases.append(origin_umo)
        item.origin_aliases = aliases

    def _backfill_protocol_fields(self, item: DiaryNotebook, origin_umo: str = "") -> bool:
        changed = False
        parts = _origin_parts(origin_umo or item.origin_umo)
        if not parts:
            parts = self._origin_parts_from_audit_tag(item.protocol_audit_tag)
        if parts:
            platform_id, message_type, session_id = parts
            if item.platform_id != platform_id:
                item.platform_id = platform_id
                changed = True
            if item.message_type != message_type:
                item.message_type = message_type
                changed = True
            if item.session_id != session_id:
                item.session_id = session_id
                changed = True
            canonical = _canonical_origin_umo(platform_id, message_type, session_id)
            if canonical and item.origin_umo != canonical:
                self._remember_origin_alias(item, item.origin_umo)
                item.origin_umo = canonical
                changed = True
        desired_tag = _protocol_audit_tag(item.platform_id, item.message_type, item.session_id)
        if desired_tag and item.protocol_audit_tag != desired_tag:
            item.protocol_audit_tag = desired_tag
            changed = True
        if item.origin_aliases is None:
            item.origin_aliases = []
            changed = True
        return changed

    def _origin_parts_from_audit_tag(self, tag: str) -> tuple[str, str, str] | None:
        parts = str(tag or "").split(":")
        if len(parts) < 6 or parts[0] != "umo" or parts[1] != "v1":
            return None
        platform_id = parts[2].strip()
        family = parts[3].strip()
        session_id = ":".join(parts[4:-1]).strip()
        raw_type = parts[-1].strip() or family
        if not platform_id or not session_id:
            return None
        return platform_id, raw_type, session_id
