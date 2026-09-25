"""0.6.0：未来任务身份、私密备忘录、日期与模块包路径安全、设置缓存。"""

from __future__ import annotations

import asyncio
import importlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

from main import PLUGIN_NAME, NestDiaryConnectorPlugin
from nest_diary_web.models import ServiceUiSettings
from nest_diary_web.diary.diary_service import DiaryService
from nest_diary_web.media.media_service import MediaService
from nest_diary_web.models import DiaryEntry
from nest_diary_web.paths import NestPaths, safe_date, strict_date
from nest_diary_web.settings_service import ServiceSettingsStore

GROUP = "aiocqhttp:GroupMessage:555"
MEMOS = {
    "m1": {"id": "m1", "title": "银行卡", "content": "密码 123456", "sensitive": True, "tags": []},
    "m2": {"id": "m2", "title": "奶茶", "content": "少冰", "sensitive": False, "tags": []},
}


def run(coro):
    return asyncio.run(coro)


class FakeEvent:
    def __init__(self, sender: str = "30003", admin: bool = False, cron_payload: dict | None = None) -> None:
        self.unified_msg_origin = GROUP
        self._sender = sender
        self._admin = admin
        self._extras = {"cron_payload": cron_payload} if cron_payload is not None else {}

    def get_sender_id(self) -> str:
        return self._sender

    def is_admin(self) -> bool:
        return self._admin

    def get_extra(self, key=None, default=None):
        return self._extras.get(key, default)


class MiniCron:
    def __init__(self, jobs: list[SimpleNamespace], running: bool = True) -> None:
        self.scheduler = SimpleNamespace(running=running)
        self.jobs = list(jobs)
        self.deleted: list[str] = []
        self.added: list[dict] = []

    async def list_jobs(self, job_type=None):
        return list(self.jobs)

    async def delete_job(self, job_id: str) -> None:
        self.deleted.append(job_id)
        self.jobs = [job for job in self.jobs if job.job_id != job_id]

    async def add_active_job(self, **kwargs):
        self.added.append(kwargs)


def cron_job(job_id: str, payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        job_id=job_id,
        name="nest_diary_daily_g1",
        description=f"{PLUGIN_NAME}:daily_archive:g1" if payload.get("managed_by") else "",
        payload=payload,
        cron_expression="0 3 * * *",
        enabled=True,
        run_once=False,
    )


def make_plugin(admin_ids: str = "", ui_settings: ServiceUiSettings | None = None, notebooks: list[dict] | None = None):
    plugin = object.__new__(NestDiaryConnectorPlugin)
    plugin.config = {"nest_admin_ids": admin_ids, "timezone": "Asia/Shanghai"}
    plugin.context = SimpleNamespace()
    plugin._future_task_sync_lock = None
    settings = ui_settings or ServiceUiSettings()
    plugin.client = SimpleNamespace(
        service_settings=SimpleNamespace(load=lambda: settings),
        memo_service=SimpleNamespace(get=lambda memo_id: SimpleNamespace(**MEMOS[memo_id]) if memo_id in MEMOS else None),
        diary_service=SimpleNamespace(list_notebooks=lambda: list(notebooks or [])),
    )

    async def resolve_notebook(origin: str) -> dict:
        return {"id": "default", "origin_umo": origin}

    async def read_memo(memo_id: str) -> dict:
        return dict(MEMOS[memo_id])

    async def search_memos(query: str = "", include_archived: bool = False) -> dict:
        return {"items": [dict(item) for item in MEMOS.values()]}

    plugin.tools = SimpleNamespace(resolve_notebook=resolve_notebook, read_memo=read_memo, search_memos=search_memos)
    return plugin


class CronIdentityTest(unittest.TestCase):
    def test_plugin_managed_job_skips_permission_checks(self) -> None:
        plugin = make_plugin(admin_ids="10001")
        event = FakeEvent(cron_payload={"managed_by": PLUGIN_NAME, "session": GROUP})
        self.assertTrue(plugin._is_managed_cron_event(event))
        self.assertEqual(run(plugin._guard_permission(event, "memo_read", "查看备忘录")), "")

    def test_user_future_task_acts_as_its_creator(self) -> None:
        plugin = make_plugin(admin_ids="10001")
        member_job = FakeEvent(cron_payload={"sender_id": "20002", "origin": "tool"})
        self.assertFalse(plugin._is_managed_cron_event(member_job))
        self.assertIn("只有小窝管理员", run(plugin._guard_permission(member_job, "memo_read", "查看备忘录")))
        admin_job = FakeEvent(cron_payload={"sender_id": "10001", "origin": "tool"})
        self.assertEqual(run(plugin._guard_permission(admin_job, "memo_read", "查看备忘录")), "")

    def test_marker_next_to_sender_id_is_not_trusted(self) -> None:
        plugin = make_plugin(admin_ids="10001")
        self.assertFalse(plugin._is_managed_cron_event(FakeEvent(cron_payload={"managed_by": PLUGIN_NAME, "sender_id": "20002"})))


class PrivateMemoTest(unittest.TestCase):
    def test_private_memo_needs_a_real_admin_even_without_nest_admins(self) -> None:
        plugin = make_plugin(admin_ids="")
        self.assertIn("只有小窝管理员可以读取", run(plugin.read_memo_tool(FakeEvent(admin=False), "m1")))
        self.assertIn("少冰", run(plugin.read_memo_tool(FakeEvent(admin=False), "m2")))
        self.assertIn("密码 123456", run(plugin.read_memo_tool(FakeEvent(admin=True), "m1")))

    def test_search_hides_private_memos_from_non_admins(self) -> None:
        plugin = make_plugin(admin_ids="10001", ui_settings=ServiceUiSettings(non_admin_permissions=["memo_read"]))
        member_view = run(plugin.search_memos_tool(FakeEvent(sender="20002"), "x"))
        self.assertNotIn("银行卡", member_view)
        self.assertIn("奶茶", member_view)
        admin_view = run(plugin.search_memos_tool(FakeEvent(sender="10001"), "x"))
        self.assertIn("银行卡", admin_view)
        self.assertNotIn("123456", admin_view)

    def test_non_admin_cannot_write_or_delete_private_memos(self) -> None:
        settings = ServiceUiSettings(non_admin_permissions=["memo_write", "memo_delete"])
        plugin = make_plugin(admin_ids="10001", ui_settings=settings)
        member = FakeEvent(sender="20002")
        self.assertEqual(run(plugin.write_memo_tool(member, "密码是 abc", sensitive=True)), "私密备忘录只有小窝管理员可以写入。")
        self.assertIn("只有小窝管理员可以删除", run(plugin.delete_memo_tool(member, "m1")))


class FutureJobTest(unittest.TestCase):
    NOTEBOOK = {"id": "g1", "name": "群一", "origin_umo": GROUP, "enabled": True, "auto_archive_enabled": True, "archive_time": "23:30"}

    def test_job_spec_carries_timezone_and_webui_rules(self) -> None:
        plugin = make_plugin(ui_settings=ServiceUiSettings(diary_write_prompt="只写真实发生的事"), notebooks=[self.NOTEBOOK])
        spec = plugin._desired_future_jobs()["nest_diary_daily_g1"]
        self.assertEqual(spec["timezone"], "Asia/Shanghai")
        self.assertEqual(spec["cron_expression"], "30 23 * * *")
        self.assertIn("只写真实发生的事", spec["payload"]["note"])
        self.assertIn("notebook_id 必须使用 g1", spec["payload"]["note"])

    def test_sync_mounts_with_timezone_and_never_touches_user_jobs(self) -> None:
        user_job = cron_job("user-1", {"sender_id": "20002", "origin": "tool"})
        manager = MiniCron([user_job])
        plugin = make_plugin(notebooks=[self.NOTEBOOK])
        plugin.context = SimpleNamespace(cron_manager=manager)
        run(plugin._sync_future_tasks())
        self.assertEqual(manager.deleted, [])
        self.assertEqual(manager.added[0]["timezone"], "Asia/Shanghai")

    def test_terminate_cleanup_only_removes_plugin_jobs(self) -> None:
        manager = MiniCron([cron_job("managed-1", {"managed_by": PLUGIN_NAME}), cron_job("user-1", {"sender_id": "20002"})])
        plugin = make_plugin()
        plugin.context = SimpleNamespace(cron_manager=manager)
        run(plugin._remove_managed_future_jobs())
        self.assertEqual(manager.deleted, ["managed-1"])

    def test_astrbot_shutdown_keeps_managed_jobs(self) -> None:
        manager = MiniCron([cron_job("managed-1", {"managed_by": PLUGIN_NAME})], running=False)
        plugin = make_plugin()
        plugin.context = SimpleNamespace(cron_manager=manager)
        run(plugin._remove_managed_future_jobs())
        self.assertEqual(manager.deleted, [])


class PathSafetyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.previous_data_dir = os.environ.get("NEST_DATA_DIR")
        if "nest_diary_web.main" not in sys.modules:
            os.environ["NEST_DATA_DIR"] = cls.temp_dir.name
        cls.web = importlib.import_module("nest_diary_web.main")

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.previous_data_dir is None:
            os.environ.pop("NEST_DATA_DIR", None)
        else:
            os.environ["NEST_DATA_DIR"] = cls.previous_data_dir
        cls.temp_dir.cleanup()

    def test_dates_stay_verbatim_and_traversal_is_rejected(self) -> None:
        self.assertEqual(safe_date("2026-9-5"), "2026-9-5")
        self.assertEqual(strict_date("2026-9-5"), "2026-9-5")
        with self.assertRaises(ValueError):
            strict_date("2026-13-40")
        paths = NestPaths(Path(self.temp_dir.name) / "nest")
        legacy = paths.diary_file_for_notebook("default", "2026-9-5")
        self.assertEqual(legacy.relative_to(paths.diary_entries_dir_for_notebook("default")).as_posix(), "2026/9/2026-9-5.md")
        with self.assertRaises(ValueError):
            paths.diary_file_for_notebook("default", "x-y-/../../../../outside")
        with self.assertRaises(ValueError):
            paths.legacy_diary_file("2026/../../evil")
        self.assertEqual(paths.diary_file_for_notebook("default", "2026-09-25").name, "2026-09-25.md")

    def test_module_zip_rejects_anchored_paths_before_touching_disk(self) -> None:
        self.assertIsNone(self.web._safe_zip_parts("/Users/Public/x.txt"))
        self.assertIsNone(self.web._safe_zip_parts("C:/Users/Public/x.txt"))
        self.assertEqual(self.web._safe_zip_parts("habit/page.js"), ("habit", "page.js"))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("module.json", "{}")
            archive.writestr("C:/evil.txt", "x")
        target = Path(self.temp_dir.name) / "evil-mod"
        with self.assertRaises(HTTPException):
            self.web._extract_module_zip(buffer.getvalue(), target, (), overwrite=False)
        self.assertFalse(target.exists())


def write_legacy_entry(path: Path, date: str, title: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {"date": date, "notebook_id": "default", "notebook_name": "默认日记本", "title": title}
    lines = ["---", *(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in meta.items()), "---", "", f"# {title}", "", body, ""]
    path.write_text("\n".join(lines), encoding="utf-8")


class LegacyDataTest(unittest.TestCase):
    """0.5.x 写下的日期名千奇百怪，升级后必须照样能列出、读取、搜索、改写和删除。"""

    def test_legacy_diary_names_stay_usable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = NestPaths(Path(tmp))
            root = paths.diary_entries_dir_for_notebook("default")
            write_legacy_entry(root / "2026" / "9" / "2026-9-5.md", "2026-9-5", "非补零日期", "去了海边")
            write_legacy_entry(root / "2026" / "13" / "2026-13-40.md", "2026-13-40", "错误日期", "月份写错了")
            write_legacy_entry(root / "2026" / "09" / "2026-09-25（周四）.md", "2026-09-25（周四）", "怪名字", "独角兽咖啡")
            service = DiaryService(paths)
            self.assertEqual({entry.title for entry in service.store.list_entries("default")}, {"非补零日期", "错误日期", "怪名字"})
            for date in ("2026-9-5", "2026-13-40", "2026-09-25（周四）"):
                self.assertTrue(service.read_by_date(date).body)
            self.assertTrue(service.search("独角兽咖啡"))

            service.write_diary(DiaryEntry(date="2026-9-5", title="改过了", body="又去了海边"))
            self.assertIn("改过了", (root / "2026" / "9" / "2026-9-5.md").read_text(encoding="utf-8"))
            with self.assertRaises(ValueError):
                service.write_diary(DiaryEntry(date="2026-13-40", title="x", body="y"))

            self.assertTrue(service.delete_diary("2026-09-25（周四）"))
            self.assertFalse((root / "2026" / "09" / "2026-09-25（周四）.md").exists())
            self.assertTrue(list((paths.revisions_dir / "default" / "legacy").glob("*/*.md")))

    def test_legacy_media_folder_is_still_found_by_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = NestPaths(Path(tmp))
            folder = paths.media_dir / "by-date" / "2026" / "09" / "2026-09-25（周四）"
            folder.mkdir(parents=True)
            asset = {"sha256": "a" * 64, "original_name": "cat.png", "date": "2026-09-25（周四）"}
            (folder / "manifest.json").write_text(json.dumps({"date": "2026-09-25（周四）", "assets": [asset]}), encoding="utf-8")
            media = MediaService(paths)
            self.assertEqual(len(media.list_by_date("2026-09-25（周四）")["assets"]), 1)
            self.assertEqual(media.list_by_date("../../x")["assets"], [])


class SettingsCacheTest(unittest.TestCase):
    def test_cached_load_returns_copies_and_sees_other_writers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = NestPaths(Path(tmp))
            store = ServiceSettingsStore(paths)
            store.save(ServiceUiSettings(site_title="甲"))
            first = store.load()
            first.site_title = "被改了"
            self.assertEqual(store.load().site_title, "甲")
            ServiceSettingsStore(paths).save(ServiceUiSettings(site_title="乙"))
            self.assertEqual(store.load().site_title, "乙")


if __name__ == "__main__":
    unittest.main()
