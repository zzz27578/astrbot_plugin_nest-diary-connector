from __future__ import annotations

from threading import RLock

from nest_diary_web.diary.markdown_store import MarkdownDiaryStore
from nest_diary_web.diary.notebook_service import NotebookService
from nest_diary_web.diary.revision_service import RevisionService
from nest_diary_web.models import DiaryEntry
from nest_diary_web.paths import NestPaths, strict_date
from nest_diary_web.search.search_service import SearchService

# The bot and embedded WebUI have separate service instances in the same process.
_DIARY_WRITE_LOCK = RLock()


class DiaryService:
    def __init__(self, paths: NestPaths):
        self.paths = paths
        self.store = MarkdownDiaryStore(paths)
        self.notebooks = NotebookService(paths)
        self.search_service = SearchService(paths)
        self.revisions = RevisionService(paths)
        self.rebuild_index()

    def write_diary(self, entry: DiaryEntry, reason: str = "", mode: str = "append") -> DiaryEntry:
        if mode not in {"append", "replace"}:
            raise ValueError("Diary write mode must be append or replace")
        if not entry.body.strip():
            raise ValueError("Diary body must not be empty")
        with _DIARY_WRITE_LOCK:
            return self._write_diary(entry, reason, mode)

    def _write_diary(self, entry: DiaryEntry, reason: str, mode: str) -> DiaryEntry:
        entry.date = strict_date(entry.date)
        notebook = self.notebooks.ensure(
            entry.notebook_id or "default",
            name=entry.notebook_name,
            origin_umo=entry.origin_umo,
            platform_id=entry.platform_id,
            message_type=entry.message_type,
            session_id=entry.session_id,
        )
        entry.notebook_id = notebook.id
        entry.notebook_name = notebook.name
        if not entry.origin_umo:
            entry.origin_umo = notebook.origin_umo
        if not entry.platform_id:
            entry.platform_id = notebook.platform_id
        if not entry.message_type:
            entry.message_type = notebook.message_type
        if not entry.session_id:
            entry.session_id = notebook.session_id
        # locate() also finds diaries in the legacy default-notebook directory.
        diary_path = self.store.locate(entry.date, entry.notebook_id)
        if diary_path.exists():
            previous = self.store.read(entry.date, entry.notebook_id)
            if mode == "append":
                old_body, new_body = previous.body.rstrip(), entry.body.strip()
                if new_body == old_body.strip() or old_body.endswith("\n\n" + new_body):
                    entry.body = old_body
                elif old_body and new_body.startswith(old_body + "\n"):
                    # Accept callers that supply the old text followed by a continuation.
                    entry.body = new_body
                else:
                    entry.body = "\n\n".join(part for part in (old_body, new_body) if part)
                entry.title = previous.title or entry.title
                for field in ("mood", "tags", "people", "media_refs"):
                    setattr(entry, field, list(dict.fromkeys(getattr(previous, field) + getattr(entry, field))))
                entry.importance = max(previous.importance, entry.importance)
                entry.source = previous.source
                for field in ("origin_umo", "platform_id", "message_type", "session_id"):
                    if not getattr(entry, field):
                        setattr(entry, field, getattr(previous, field))
                entry.revision = previous.revision
                if entry == previous:
                    return previous
            entry.revision = previous.revision + 1
            self.revisions.snapshot(
                date=entry.date,
                content=diary_path.read_text(encoding="utf-8"),
                reason=reason or f"{mode} diary entry",
                source="write_diary",
                notebook_id=entry.notebook_id,
            )
        self.store.write(entry)
        self.search_service.upsert_entry(entry)
        return entry

    def read_by_date(self, date: str, notebook_id: str = "default") -> DiaryEntry:
        return self.store.read(date, notebook_id=notebook_id)

    def delete_diary(self, date: str, reason: str = "", notebook_id: str = "default") -> bool:
        try:
            diary_path = self.store.locate(date, notebook_id)
        except FileNotFoundError:
            return False
        if not diary_path.exists():
            return False
        self.revisions.snapshot(
            date=date,
            content=diary_path.read_text(encoding="utf-8"),
            reason=reason or "delete diary entry",
            source="delete_diary",
            notebook_id=notebook_id,
        )
        deleted = self.store.delete(date, notebook_id=notebook_id)
        self.search_service.delete_entry(date, notebook_id=notebook_id)
        return deleted

    def search(self, query: str, top_k: int = 8, snippet_chars: int = 180, notebook_id: str | None = None) -> list[dict]:
        return self.search_service.search(query=query, top_k=top_k, snippet_chars=snippet_chars, notebook_id=notebook_id)

    def search_status(self) -> dict:
        capabilities = self.search_service.capabilities
        return {"backend": capabilities.backend, "fts5": capabilities.fts5}

    def list_entries(self, notebook_id: str | None = None) -> list[DiaryEntry]:
        return self.store.list_entries(notebook_id=notebook_id)

    def archive_tree(self, notebook_id: str | None = None) -> list[dict]:
        return self.store.archive_tree(notebook_id=notebook_id)

    def list_notebooks(self) -> list[dict]:
        return [item.__dict__ for item in self.notebooks.list_notebooks()]

    def save_notebooks(
        self,
        notebooks: list[dict],
        delete_ids: list[str] | None = None,
        replace: bool = False,
    ) -> list[dict]:
        saved = self.notebooks.save_notebooks(notebooks, delete_ids=delete_ids or [], replace=replace)
        self.rebuild_index()
        return [item.__dict__ for item in saved]

    def resolve_notebook_from_origin(self, origin_umo: str) -> dict:
        return self.notebooks.resolve_from_origin(origin_umo).__dict__

    def bind_notebook_origin(self, notebook_id: str, origin_umo: str) -> dict:
        notebook = self.notebooks.bind_origin(notebook_id, origin_umo)
        self.rebuild_index()
        return notebook.__dict__

    def rebuild_index(self) -> int:
        entries = self.store.list_entries()
        return self.search_service.sync_entries(entries)
