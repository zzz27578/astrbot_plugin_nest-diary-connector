"""Exercise old-version data, upgrade, rollback reads, and backup restoration.

Run: python scripts/verify_upgrade_compatibility.py --old-ref 93c168c
The old checkout and all data are created in temporary directories.
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile


WORKER = r'''
import json
from dataclasses import asdict
from pathlib import Path
import sys
from nest_diary_web.diary.diary_service import DiaryService
from nest_diary_web.models import DiaryEntry
from nest_diary_web.memos.memo_service import MemoService
from nest_diary_web.paths import NestPaths
from nest_diary_web.settings_service import ServiceSettingsStore
from nest_diary_web.backup_service import BackupService

action, directory, archive = sys.argv[1:]
paths = NestPaths(Path(directory))
diary = DiaryService(paths)
memos = MemoService(paths)
settings = ServiceSettingsStore(paths)
if action == "seed":
    diary.write_diary(DiaryEntry(date="2026-10-22", title="旧版一天", body="上午散步，晚上看电影。", tags=["旧版"], people=["主人"], media_refs=["old.png"]))
    diary.write_diary(DiaryEntry(date="2026-9-5", title="非补零日期", body="旧日期正文。"))
    diary.write_diary(DiaryEntry(date="2026-10-22", title="群聊", body="其他群旧文。", notebook_id="group-2"))
    memos.create(title="私密旧纸条", content="旧版私密正文。", tags=["私人"], sensitive=True, pinned=True, source_chat="私聊")
    memos.create(title="归档旧纸条", content="旧版归档正文。", archived=True, source_chat="群聊")
    saved = settings.load()
    saved.site_title = "旧版小窝"
    saved.diary_write_prompt = "自定义规范保持原样。"
    saved.memos_write_policy = "review"
    settings.save(saved)
    Path(archive).write_bytes(BackupService(paths).export_zip(nest_version="0.6.0"))
elif action == "append":
    diary.write_diary(DiaryEntry(date="2026-10-22", body="下午做饭。"))
elif action == "restore":
    BackupService(paths).import_zip(Path(archive).read_bytes(), strategy="safe")
    diary.rebuild_index()
result = {"diaries": [asdict(item) for item in diary.list_entries()],
          "notebooks": diary.list_notebooks(),
          "memos": [asdict(item) for item in memos.list_memos(include_archived=True, include_deleted=True)],
          "settings": asdict(settings.load())}
print(json.dumps(result, ensure_ascii=False, sort_keys=True))
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-ref", default="93c168c")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    archive_bytes = subprocess.check_output(["git", "archive", "--format=tar", args.old_ref], cwd=root)
    with tempfile.TemporaryDirectory(prefix="nest-upgrade-") as temp:
        directory = Path(temp)
        old = directory / "old"
        old.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive_bytes)) as archive:
            archive.extractall(old)
        data, restored, backup = directory / "data", directory / "restored", directory / "backup.zip"

        def run(checkout, action, target=data):
            output = subprocess.check_output([sys.executable, "-X", "utf8", "-c", WORKER, action, str(target), str(backup)], cwd=checkout)
            return json.loads(output.decode("utf-8"))

        def files():
            return {str(p.relative_to(data)): p.read_bytes() for p in data.rglob("*") if p.is_file() and p.suffix not in {".sqlite", ".db", ".sqlite3"} and not p.name.endswith(("-wal", "-shm"))}

        before = run(old, "seed")
        original_files = files()
        upgraded = run(root, "read")
        assert upgraded == before, "Upgrade changed the loaded data"
        assert files() == original_files, "Upgrade changed original data files"
        restore = run(root, "restore", restored)
        assert restore == before, "Old backup did not restore identically"
        appended = run(root, "append")
        old_read = run(old, "read")
        assert old_read == appended, "Old version could not read new-version data"
        diary = next(item for item in appended["diaries"] if item["date"] == "2026-10-22" and item["notebook_id"] == "default")
        assert diary["body"] == "上午散步，晚上看电影。\n\n下午做饭。"
        assert appended["memos"] == before["memos"], "Diary append changed memos"
        print(json.dumps({"status": "ok", "old_ref": args.old_ref, "diaries": len(before["diaries"]), "memos": len(before["memos"]), "checks": ["upgrade reads", "original file bytes", "old backup restore", "rollback reads", "append preserves original", "memos unchanged"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
