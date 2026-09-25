from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from nest_diary_web.models import DiaryEntry, PersonImpression
from nest_diary_web.paths import NestPaths, atomic_write_text


class ImpressionService:
    AUTO_EVIDENCE_NOTE_RE = re.compile(r"^自动补充证据：该人物出现在 (\d{4}-\d{2}-\d{2}) 的日记关联人物中。$")
    AUTO_NOTE_RE = re.compile(r"^自动(?:补充证据|候选建档)：该人物出现在 \d{4}-\d{2}-\d{2} 的日记关联人物中。$")

    def __init__(self, paths: NestPaths):
        self.paths = paths
        self.paths.ensure_all()
        self.people_dir.mkdir(parents=True, exist_ok=True)
        self.purge_auto_candidates()

    @property
    def people_dir(self) -> Path:
        return self.paths.memory_dir / "people"

    def save(
        self,
        impression: PersonImpression,
        identity_strategy: str = "separate",
        source_chat: str = "",
        merge_existing: bool = False,
    ) -> PersonImpression:
        identity_strategy = identity_strategy if identity_strategy in {"unified", "nested", "separate"} else "separate"
        if identity_strategy in {"unified", "nested"} and not str(impression.qq_id or "").strip():
            current_same_name = self.get(impression.name)
            if current_same_name and not self._is_auto_candidate(current_same_name):
                impression.qq_id = current_same_name.qq_id
            elif not current_same_name or self._is_auto_candidate(current_same_name):
                raise ValueError("Unified impression identity requires qq_id for a new nickname")
        impression = self._apply_identity_strategy(impression, identity_strategy, source_chat)
        current = self.get(impression.name)
        if current:
            if merge_existing:
                impression = self._merge_existing_profile(current, impression)
            if not impression.qq_id:
                impression.qq_id = current.qq_id
            if not impression.group_impressions:
                impression.group_impressions = current.group_impressions
        if current and not impression.updated_at:
            impression.updated_at = self._now()
        elif not impression.updated_at:
            impression.updated_at = self._now()

        impression.name = impression.name.strip()
        if not impression.name:
            raise ValueError("Person name is required")
        impression.qq_id = str(impression.qq_id or "").strip()
        impression.group_impressions = [item for item in impression.group_impressions if isinstance(item, dict)]
        impression.summary = impression.summary.strip()
        impression.identity = impression.identity.strip()
        impression.relationship = impression.relationship.strip()
        impression.special_comment = impression.special_comment.strip()
        impression.notes = impression.notes.strip()
        impression = self._sanitize_impression(impression)
        impression.affinity = max(1, min(int(impression.affinity), 5))
        impression.confidence = max(1, min(int(impression.confidence), 5))
        path = self._person_path(impression.name)
        atomic_write_text(path, json.dumps(asdict(impression), ensure_ascii=False, indent=2))
        return impression

    def _apply_identity_strategy(self, impression: PersonImpression, strategy: str, source_chat: str = "") -> PersonImpression:
        strategy = strategy if strategy in {"unified", "nested", "separate"} else "separate"
        qq_id = str(impression.qq_id or "").strip()
        if not qq_id or strategy == "separate":
            return impression
        current_same_name = self.get(impression.name)
        target_by_qq = self.find_by_qq_id(qq_id)
        target = target_by_qq or current_same_name
        if not target:
            return impression
        if target.name != impression.name:
            self._remember_alias(target, impression.name)
        if (
            target_by_qq
            and current_same_name
            and current_same_name.name != target_by_qq.name
            and self._is_auto_candidate(current_same_name)
        ):
            self.delete(current_same_name.name)
        if strategy == "nested":
            target = self._merge_impression_fields(target, impression, merge_summary=False)
            target.qq_id = qq_id
            target.group_impressions = self._upsert_group_impression(target.group_impressions, impression, source_chat)
            return target
        target = self._merge_impression_fields(target, impression, merge_summary=False)
        if impression.summary.strip():
            target.summary = impression.summary.strip()
        target.qq_id = qq_id
        return target

    def delete(self, name: str) -> bool:
        path = self._person_path(name)
        if not path.exists():
            return False
        path.unlink()
        return True

    def get(self, name: str) -> PersonImpression | None:
        path = self._person_path(name)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return self._from_dict(data)

    def list_people(self) -> list[PersonImpression]:
        people: list[PersonImpression] = []
        for path in sorted(self.people_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                people.append(self._from_dict(data))
            except Exception:
                continue
        return sorted(people, key=lambda item: item.updated_at, reverse=True)

    def find_by_qq_id(self, qq_id: str) -> PersonImpression | None:
        qq_id = str(qq_id or "").strip()
        if not qq_id:
            return None
        for item in self.list_people():
            if str(item.qq_id or "").strip() == qq_id:
                return item
        return None

    def touch_from_diary(
        self,
        entry: DiaryEntry,
        *,
        allow_new_people: bool = False,
        update_existing: bool = False,
        min_confidence: int = 3,
    ) -> list[PersonImpression]:
        # Diary people tags do not contain a trustworthy QQ identity or a
        # rewritten long-term summary. Creating placeholder profiles here can
        # split one real person into multiple nickname files, especially when
        # the configured identity strategy is unified or nested. Actual
        # creation and updates must go through write_impression after the bot
        # reads the existing profile and produces a substantive rewrite.
        return []

    def _from_dict(self, data: dict) -> PersonImpression:
        return self._sanitize_impression(PersonImpression(
            name=data["name"],
            summary=data.get("summary", ""),
            qq_id=str(data.get("qq_id", "") or ""),
            group_impressions=data.get("group_impressions", []) if isinstance(data.get("group_impressions", []), list) else [],
            identity=data.get("identity", ""),
            traits=data.get("traits", []),
            hobbies=data.get("hobbies", []),
            interests=data.get("interests", []),
            preferences=data.get("preferences", []),
            relationship=data.get("relationship", ""),
            affinity=data.get("affinity", 3),
            special_comment=data.get("special_comment", ""),
            evidence_dates=data.get("evidence_dates", []),
            confidence=data.get("confidence", 3),
            notes=data.get("notes", ""),
            updated_at=data.get("updated_at", ""),
        ))

    def _merge_impression_fields(self, base: PersonImpression, incoming: PersonImpression, merge_summary: bool) -> PersonImpression:
        if merge_summary and incoming.summary and incoming.summary not in base.summary:
            base.summary = f"{base.summary.rstrip()}\n\n【{incoming.name}】{incoming.summary}".strip() if base.summary else incoming.summary
        for field_name in ("traits", "hobbies", "interests", "preferences", "evidence_dates"):
            merged = list(dict.fromkeys([*getattr(base, field_name), *getattr(incoming, field_name)]))
            setattr(base, field_name, merged)
        for field_name in ("identity", "relationship", "special_comment", "notes"):
            current = getattr(base, field_name)
            value = getattr(incoming, field_name)
            if value and value not in current:
                setattr(base, field_name, f"{current.rstrip()}\n{value}".strip() if current else value)
        base.affinity = max(int(base.affinity or 3), int(incoming.affinity or 3))
        base.confidence = max(int(base.confidence or 3), int(incoming.confidence or 3))
        return base

    def _merge_existing_profile(self, current: PersonImpression, incoming: PersonImpression) -> PersonImpression:
        current = self._sanitize_impression(current)
        incoming = self._sanitize_impression(incoming)
        for field_name in ("traits", "hobbies", "interests", "preferences", "evidence_dates"):
            merged = list(dict.fromkeys([*getattr(current, field_name), *getattr(incoming, field_name)]))
            setattr(incoming, field_name, merged)
        for field_name in ("summary", "identity", "relationship", "special_comment"):
            if not getattr(incoming, field_name):
                setattr(incoming, field_name, getattr(current, field_name))
        incoming.notes = self._merge_text_lines(current.notes, incoming.notes)
        incoming.affinity = max(int(current.affinity or 3), int(incoming.affinity or 3))
        incoming.confidence = max(int(current.confidence or 3), int(incoming.confidence or 3))
        return incoming

    def _merge_text_lines(self, current: str, incoming: str) -> str:
        lines = []
        for value in (current, incoming):
            for line in str(value or "").splitlines():
                line = line.strip()
                if line and line not in lines:
                    lines.append(line)
        return "\n".join(lines)

    def _sanitize_impression(self, impression: PersonImpression) -> PersonImpression:
        auto_evidence_dates: set[str] = set()
        clean_note_lines: list[str] = []
        for line in str(impression.notes or "").splitlines():
            line = line.strip()
            if not line:
                continue
            match = self.AUTO_EVIDENCE_NOTE_RE.match(line)
            if match:
                auto_evidence_dates.add(match.group(1))
                continue
            if self.AUTO_NOTE_RE.match(line):
                continue
            clean_note_lines.append(line)
        impression.notes = "\n".join(dict.fromkeys(clean_note_lines))
        impression.evidence_dates = self._normalize_list(
            date
            for date in impression.evidence_dates
            if str(date).strip() not in auto_evidence_dates
        )
        for field_name in ("traits", "hobbies", "interests", "preferences"):
            setattr(impression, field_name, self._normalize_list(getattr(impression, field_name)))
        return impression

    def _normalize_list(self, values) -> list[str]:
        return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))

    def purge_auto_candidates(self) -> int:
        removed = 0
        for path in self.people_dir.glob("*.json"):
            try:
                item = self._from_dict(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
            if self._is_auto_candidate(item):
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    def _is_auto_candidate(self, item: PersonImpression) -> bool:
        return (
            not str(item.qq_id or "").strip()
            and "这是按当前印象策略生成的候选档案" in str(item.summary or "")
            and not any(
                [
                    item.identity,
                    item.traits,
                    item.hobbies,
                    item.interests,
                    item.preferences,
                    item.relationship,
                    item.special_comment,
                    item.notes,
                    item.group_impressions,
                ]
            )
        )

    def _remember_alias(self, item: PersonImpression, alias: str) -> None:
        alias = str(alias or "").strip()
        if not alias or alias == item.name:
            return
        marker = f"历史昵称：{alias}"
        if marker not in item.notes:
            item.notes = f"{item.notes.rstrip()}\n{marker}".strip()

    def _upsert_group_impression(self, items: list[dict], incoming: PersonImpression, source_chat: str) -> list[dict]:
        source_chat = str(source_chat or "").strip() or "未知群聊"
        next_items = [dict(item) for item in items if isinstance(item, dict)]
        for item in next_items:
            if str(item.get("source_chat") or "") == source_chat:
                item.update(
                    {
                        "name": incoming.name,
                        "summary": incoming.summary,
                        "identity": incoming.identity,
                        "relationship": incoming.relationship,
                        "traits": incoming.traits,
                        "hobbies": incoming.hobbies,
                        "interests": incoming.interests,
                        "preferences": incoming.preferences,
                        "special_comment": incoming.special_comment,
                        "evidence_dates": incoming.evidence_dates,
                        "confidence": incoming.confidence,
                        "updated_at": self._now(),
                    }
                )
                return next_items
        next_items.append(
            {
                "source_chat": source_chat,
                "name": incoming.name,
                "summary": incoming.summary,
                "identity": incoming.identity,
                "relationship": incoming.relationship,
                "traits": incoming.traits,
                "hobbies": incoming.hobbies,
                "interests": incoming.interests,
                "preferences": incoming.preferences,
                "special_comment": incoming.special_comment,
                "evidence_dates": incoming.evidence_dates,
                "confidence": incoming.confidence,
                "updated_at": self._now(),
            }
        )
        return next_items

    def _person_path(self, name: str) -> Path:
        safe_name = quote(name.strip(), safe="")
        return self.people_dir / f"{safe_name}.json"

    def _now(self) -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def _auto_summary(self, name: str, date: str, confidence: int) -> str:
        return (
            f"{name} 在 {date} 的日记中被记录为相关人物。"
            f"这是按当前印象策略生成的候选档案，置信度 {confidence}/5；"
            "需要后续日记和 bot 主观评价补充后，才应写入稳定印象。"
        )

    def _append_auto_note(self, notes: str, date: str, reason: str) -> str:
        marker = f"{reason}：该人物出现在 {date} 的日记关联人物中。"
        if marker in notes:
            return notes
        return f"{notes.rstrip()}\n{marker}".strip()
