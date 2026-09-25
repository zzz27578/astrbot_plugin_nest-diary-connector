from __future__ import annotations

import json
from pathlib import Path

from nest_diary_web.models import DiaryEntry
from nest_diary_web.paths import NestPaths, atomic_write_text


class MarkdownDiaryStore:
    def __init__(self, paths: NestPaths):
        self.paths = paths
        self.paths.ensure_all()

    def write(self, entry: DiaryEntry) -> Path:
        path = self.paths.diary_file_for_notebook(entry.notebook_id, entry.date)
        path.parent.mkdir(parents=True, exist_ok=True)
        frontmatter = {
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
        }
        lines = ["---"]
        for key, value in frontmatter.items():
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
        lines.extend(["---", "", f"# {entry.normalized_title()}", "", entry.body.rstrip(), ""])
        atomic_write_text(path, "\n".join(lines))
        return path

    def read(self, date: str, notebook_id: str = "default") -> DiaryEntry:
        path = self.paths.diary_file_for_notebook(notebook_id, date)
        if not path.exists() and notebook_id == "default":
            path = self.paths.legacy_diary_file(date)
        text = path.read_text(encoding="utf-8")
        _prefix, meta_text, body_text = text.split("---", 2)
        meta = {}
        for line in meta_text.strip().splitlines():
            key, raw_value = line.split(":", 1)
            meta[key.strip()] = json.loads(raw_value.strip())

        body_lines = body_text.strip().splitlines()
        if body_lines and body_lines[0].startswith("# "):
            body_lines = body_lines[1:]
        body = "\n".join(body_lines).strip()
        return DiaryEntry(
            date=meta["date"],
            notebook_id=meta.get("notebook_id", notebook_id),
            notebook_name=meta.get("notebook_name", "默认日记本"),
            origin_umo=meta.get("origin_umo", ""),
            platform_id=meta.get("platform_id", ""),
            message_type=meta.get("message_type", ""),
            session_id=meta.get("session_id", ""),
            title=meta.get("title"),
            mood=meta.get("mood", []),
            tags=meta.get("tags", []),
            people=meta.get("people", []),
            media_refs=meta.get("media_refs", []),
            importance=meta.get("importance", 3),
            source=meta.get("source", "bot"),
            revision=meta.get("revision", 1),
            body=body,
        )

    def list_entries(self, notebook_id: str | None = None) -> list[DiaryEntry]:
        entries: list[DiaryEntry] = []
        roots = []
        if notebook_id:
            roots.append(self.paths.diary_entries_dir_for_notebook(notebook_id))
            if notebook_id == "default":
                roots.append(self.paths.diary_dir)
        else:
            roots.extend(path / "entries" for path in self.paths.diary_notebooks_dir.iterdir() if path.is_dir())
            roots.append(self.paths.diary_dir)
        seen: set[tuple[str, str]] = set()
        for root in roots:
            if not root.exists():
                continue
            current_notebook = root.parent.name if root.parent.parent == self.paths.diary_notebooks_dir else "default"
            for path in sorted(root.glob("*/*/*.md"), reverse=True):
                key = (current_notebook, path.stem)
                if key in seen:
                    continue
                seen.add(key)
                try:
                    entries.append(self.read(path.stem, current_notebook))
                except Exception:
                    continue
        return sorted(entries, key=lambda entry: (entry.date, entry.notebook_name, entry.notebook_id), reverse=True)

    def delete(self, date: str, notebook_id: str = "default") -> bool:
        path = self.paths.diary_file_for_notebook(notebook_id, date)
        if not path.exists() and notebook_id == "default":
            path = self.paths.legacy_diary_file(date)
        if not path.exists():
            return False
        path.unlink()
        return True

    def archive_tree(self, notebook_id: str | None = None) -> list[dict]:
        years: list[dict] = []
        entries = self.list_entries(notebook_id)
        by_year: dict[str, dict[str, list[DiaryEntry]]] = {}
        for entry in entries:
            by_year.setdefault(entry.date[:4], {}).setdefault(entry.date[:7], []).append(entry)
        for year in sorted(by_year.keys(), reverse=True):
            months = []
            for month in sorted(by_year[year].keys(), reverse=True):
                days = [
                    {
                        "date": entry.date,
                        "title": entry.normalized_title(),
                        "importance": entry.importance,
                        "tags": entry.tags,
                        "people": entry.people,
                        "notebook_id": entry.notebook_id,
                        "notebook_name": entry.notebook_name,
                    }
                    for entry in sorted(by_year[year][month], key=lambda item: item.date, reverse=True)
                ]
                if days:
                    months.append({"month": month, "days": days, "count": len(days)})
            if months:
                years.append({"year": year, "months": months, "count": sum(month["count"] for month in months)})
        return years
