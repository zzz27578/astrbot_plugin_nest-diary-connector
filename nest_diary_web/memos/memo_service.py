from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nest_diary_web.models import MemoEntry
from nest_diary_web.paths import NestPaths, safe_package_id, atomic_write_text


class MemoService:
    def __init__(self, paths: NestPaths):
        self.paths = paths
        self.paths.ensure_all()
        self.path = self.paths.memos_dir / "items.json"

    def list_memos(
        self,
        *,
        query: str = "",
        include_archived: bool = False,
        include_deleted: bool = False,
        source_chat: str = "",
        tag: str = "",
    ) -> list[MemoEntry]:
        query = (query or "").strip().lower()
        source_chat = (source_chat or "").strip()
        tag = (tag or "").strip().lower()
        items = self._load_items()
        filtered: list[MemoEntry] = []
        for item in items:
            if item.deleted_at and not include_deleted:
                continue
            if item.archived and not include_archived:
                continue
            if source_chat and source_chat not in {item.source_chat, item.origin_umo, item.session_id}:
                continue
            if tag and tag not in {entry_tag.lower() for entry_tag in item.tags}:
                continue
            haystack = "\n".join([item.title, item.content, item.source_chat, item.recorder, item.source, *item.tags]).lower()
            if query and query not in haystack:
                continue
            filtered.append(item)
        return sorted(
            filtered,
            key=lambda item: (
                not item.pinned,
                -(self._timestamp_for_sort(item.updated_at or item.created_at)),
            ),
        )

    def get(self, memo_id: str, *, include_deleted: bool = False) -> MemoEntry | None:
        memo_id = safe_package_id(memo_id, "")
        if not memo_id:
            return None
        for item in self._load_items():
            if item.id == memo_id and (include_deleted or not item.deleted_at):
                return item
        return None

    def create(
        self,
        *,
        title: str,
        content: str,
        tags: list[str] | None = None,
        source_chat: str = "",
        origin_umo: str = "",
        platform_id: str = "",
        message_type: str = "",
        session_id: str = "",
        recorder: str = "human",
        source: str = "manual",
        sensitive: bool = False,
        pinned: bool = False,
        archived: bool = False,
    ) -> MemoEntry:
        now = self._now()
        memo = MemoEntry(
            id=self._new_id(),
            title=(title or "").strip() or self._title_from_content(content),
            content=(content or "").strip(),
            tags=self._clean_tags(tags or []),
            source_chat=(source_chat or "").strip(),
            origin_umo=(origin_umo or "").strip(),
            platform_id=(platform_id or "").strip(),
            message_type=(message_type or "").strip(),
            session_id=(session_id or "").strip(),
            recorder=(recorder or "human").strip() or "human",
            source=(source or "manual").strip() or "manual",
            sensitive=bool(sensitive),
            pinned=bool(pinned),
            archived=bool(archived),
            created_at=now,
            updated_at=now,
        )
        if not memo.content:
            raise ValueError("Memo content is required")
        items = self._load_items()
        items.append(memo)
        self._save_items(items)
        return memo

    def update(
        self,
        memo_id: str,
        *,
        title: str | None = None,
        content: str | None = None,
        tags: list[str] | None = None,
        source_chat: str | None = None,
        sensitive: bool | None = None,
        pinned: bool | None = None,
        archived: bool | None = None,
    ) -> MemoEntry:
        items = self._load_items()
        memo = next((item for item in items if item.id == safe_package_id(memo_id, "")), None)
        if not memo or memo.deleted_at:
            raise ValueError("Memo not found")
        if title is not None:
            memo.title = title.strip() or self._title_from_content(memo.content)
        if content is not None:
            memo.content = content.strip()
            if not memo.content:
                raise ValueError("Memo content is required")
            if not memo.title:
                memo.title = self._title_from_content(memo.content)
        if tags is not None:
            memo.tags = self._clean_tags(tags)
        if source_chat is not None:
            memo.source_chat = source_chat.strip()
        if sensitive is not None:
            memo.sensitive = bool(sensitive)
        if pinned is not None:
            memo.pinned = bool(pinned)
        if archived is not None:
            memo.archived = bool(archived)
        memo.updated_at = self._now()
        self._save_items(items)
        return memo

    def archive(self, memo_id: str, archived: bool = True) -> MemoEntry:
        return self.update(memo_id, archived=archived)

    def delete(self, memo_id: str, *, hard: bool = False) -> bool:
        memo_id = safe_package_id(memo_id, "")
        items = self._load_items()
        before = len(items)
        if hard:
            items = [item for item in items if item.id != memo_id]
            self._save_items(items)
            return len(items) != before
        changed = False
        now = self._now()
        for item in items:
            if item.id == memo_id and not item.deleted_at:
                item.deleted_at = now
                item.updated_at = now
                changed = True
        if changed:
            self._save_items(items)
        return changed

    def count_saved_since(self, hours: int = 12, *, recorder: str = "") -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, int(hours)))
        total = 0
        recorder = (recorder or "").strip()
        for item in self._load_items():
            if item.deleted_at:
                continue
            if recorder and item.recorder != recorder:
                continue
            try:
                created = datetime.fromisoformat((item.created_at or "").replace("Z", "+00:00"))
            except ValueError:
                continue
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created >= cutoff:
                total += 1
        return total

    def summary(self) -> dict:
        items = self._load_items()
        visible = [item for item in items if not item.deleted_at]
        return {
            "count": len(visible),
            "pinned": len([item for item in visible if item.pinned]),
            "archived": len([item for item in visible if item.archived]),
            "sensitive": len([item for item in visible if item.sensitive]),
        }

    def _load_items(self) -> list[MemoEntry]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []
        raw_items = data.get("items") if isinstance(data, dict) else data
        if not isinstance(raw_items, list):
            return []
        items: list[MemoEntry] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            try:
                items.append(self._from_dict(raw))
            except Exception:
                continue
        return items

    def _save_items(self, items: list[MemoEntry]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "updated_at": self._now(),
            "items": [asdict(item) for item in items],
        }
        atomic_write_text(self.path, json.dumps(payload, ensure_ascii=False, indent=2))

    def _from_dict(self, data: dict) -> MemoEntry:
        return MemoEntry(
            id=safe_package_id(str(data.get("id") or ""), ""),
            title=str(data.get("title") or ""),
            content=str(data.get("content") or ""),
            tags=self._clean_tags(data.get("tags") if isinstance(data.get("tags"), list) else []),
            source_chat=str(data.get("source_chat") or ""),
            origin_umo=str(data.get("origin_umo") or ""),
            platform_id=str(data.get("platform_id") or ""),
            message_type=str(data.get("message_type") or ""),
            session_id=str(data.get("session_id") or ""),
            recorder=str(data.get("recorder") or "human"),
            source=str(data.get("source") or "manual"),
            sensitive=bool(data.get("sensitive", False)),
            pinned=bool(data.get("pinned", False)),
            archived=bool(data.get("archived", False)),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            deleted_at=str(data.get("deleted_at") or ""),
        )

    def _clean_tags(self, tags: list[str]) -> list[str]:
        return list(dict.fromkeys(str(tag).strip() for tag in tags if str(tag).strip()))[:16]

    def _title_from_content(self, content: str) -> str:
        one_line = " ".join((content or "").strip().split())
        return one_line[:28] or "新备忘"

    def _timestamp_for_sort(self, value: str) -> float:
        try:
            when = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            return when.timestamp()
        except Exception:
            return 0.0

    def _new_id(self) -> str:
        return "memo-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")

    def _now(self) -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
