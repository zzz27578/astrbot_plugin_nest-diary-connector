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

    def locate(self, date: str, notebook_id: str = "default") -> Path:
        try:
            path = self.paths.diary_file_for_notebook(notebook_id, date)
            if not path.exists() and notebook_id == "default":
                path = self.paths.legacy_diary_file(date)
            return path
        except ValueError:
            # Older versions accepted any "a-b-c" date; those files stay reachable by their exact name.
            for root in self._roots(notebook_id):
                for candidate in root.glob("*/*/*.md"):
                    if candidate.stem == date:
                        return candidate
            raise FileNotFoundError(f"Diary entry not found: {date}") from None

    def read(self, date: str, notebook_id: str = "default") -> DiaryEntry:
        return self._read_path(self.locate(date, notebook_id), notebook_id)

    def _roots(self, notebook_id: str | None) -> list[Path]:
        roots = []
        if notebook_id:
            roots.append(self.paths.diary_entries_dir_for_notebook(notebook_id))
            if notebook_id == "default":
                roots.append(self.paths.diary_dir)
        else:
            roots.extend(path / "entries" for path in self.paths.diary_notebooks_dir.iterdir() if path.is_dir())
            roots.append(self.paths.diary_dir)
        return roots

    def _read_path(self, path: Path, notebook_id: str) -> DiaryEntry:
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
        seen: set[tuple[str, str]] = set()
        for root in self._roots(notebook_id):
            if not root.exists():
                continue
            current_notebook = root.parent.name if root.parent.parent == self.paths.diary_notebooks_dir else "default"
            for path in sorted(root.glob("*/*/*.md"), reverse=True):
                key = (current_notebook, path.stem)
                if key in seen:
                    continue
                seen.add(key)
                try:
                    entries.append(self._read_path(path, current_notebook))
                except Exception:
                    continue
        return sorted(entries, key=lambda entry: (entry.date, entry.notebook_name, entry.notebook_id), reverse=True)

    def delete(self, date: str, notebook_id: str = "default") -> bool:
        try:
            path = self.locate(date, notebook_id)
        except FileNotFoundError:
            return False
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
