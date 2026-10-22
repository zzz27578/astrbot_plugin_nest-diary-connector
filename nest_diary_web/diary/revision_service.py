from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from nest_diary_web.paths import NestPaths, atomic_write_text


class RevisionService:
    def __init__(self, paths: NestPaths):
        self.paths = paths
        self.paths.ensure_all()

    def snapshot(self, date: str, content: str, reason: str, source: str, notebook_id: str = "default") -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target_dir = self.paths.revision_dir_for_notebook(notebook_id, date)
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{timestamp}.md"
        metadata = {
            "date": date,
            "notebook_id": notebook_id,
            "created_at_utc": timestamp,
            "source": source,
            "reason": reason,
        }
        lines = ["---"]
        for key, value in metadata.items():
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
        lines.extend(["---", "", content.rstrip(), ""])
        atomic_write_text(path, "\n".join(lines))
        return path

    def list_revisions(self) -> list[dict]:
        root = self.paths.revisions_dir
        if not root.exists():
            return []
        revisions = []
        for path in sorted(root.glob("*/*/*/*.md"), reverse=True):
            revisions.append(
                {
                    "date": path.parent.name,
                    "name": path.name,
                    "path": str(path),
                    "size": path.stat().st_size,
                }
            )
        return revisions
