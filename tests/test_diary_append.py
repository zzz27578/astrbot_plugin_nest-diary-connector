from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from main import EmbeddedNestClient, NestDiaryTools, DIARY_APPEND_POLICY
from nest_diary_web.diary.diary_service import DiaryService
from nest_diary_web.models import DiaryEntry
from nest_diary_web.paths import NestPaths


class DiaryAppendTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = NestPaths(Path(self.temp.name))
        self.service = DiaryService(self.paths)

    def write(self, body, **kwargs):
        return self.service.write_diary(DiaryEntry(date="2026-10-22", body=body, **kwargs))

    def test_second_and_third_writes_keep_morning_and_merge_metadata(self):
        self.write("上午一起散步。", title="一起度过的一天", tags=["散步"], people=["主人"], media_refs=["morning.png"], mood=["开心"], importance=5)
        self.write("下午一起做饭。", title="下午", tags=["做饭", "散步"], people=["朋友"], media_refs=["dinner.png"], mood=["满足"])
        saved = self.write("晚上看电影。")
        self.assertEqual(saved.body, "上午一起散步。\n\n下午一起做饭。\n\n晚上看电影。")
        self.assertEqual(saved.title, "一起度过的一天")
        self.assertEqual(saved.tags, ["散步", "做饭"])
        self.assertEqual(saved.people, ["主人", "朋友"])
        self.assertEqual(saved.media_refs, ["morning.png", "dinner.png"])
        self.assertEqual(saved.mood, ["开心", "满足"])
        self.assertEqual(saved.importance, 5)
        self.assertEqual(saved.revision, 3)
        snapshots = list(self.paths.revision_dir_for_notebook("default", saved.date).glob("*.md"))
        self.assertEqual(len(snapshots), 2)
        self.assertTrue(any("上午一起散步。\n\n下午一起做饭。" in p.read_text(encoding="utf-8") for p in snapshots))
        self.assertTrue(self.service.search("散步"))
        self.assertTrue(self.service.search("做饭"))

    def test_immediate_retry_and_full_continuation_do_not_repeat_old_body(self):
        self.write("上午。")
        self.write("下午。")
        saved = self.write("下午。")
        self.assertEqual(saved.body, "上午。\n\n下午。")
        self.assertEqual(saved.revision, 2)
        saved = self.write("上午。\n\n下午。\n\n晚上。")
        self.assertEqual(saved.body, "上午。\n\n下午。\n\n晚上。")

    def test_explicit_replace_still_edits_and_snapshots_the_whole_entry(self):
        self.write("上午。", title="旧标题", tags=["旧"])
        saved = self.service.write_diary(DiaryEntry(date="2026-10-22", title="修正标题", body="完整更正。"), mode="replace")
        self.assertEqual(saved.body, "完整更正。")
        self.assertEqual(saved.title, "修正标题")
        self.assertEqual(saved.tags, [])
        snapshot = next(self.paths.revision_dir_for_notebook("default", saved.date).glob("*.md"))
        self.assertIn("上午。", snapshot.read_text(encoding="utf-8"))

    def test_other_notebooks_and_dates_remain_independent(self):
        self.write("上午。")
        self.write("另一个群。", notebook_id="group-2")
        self.service.write_diary(DiaryEntry(date="2026-10-21", body="昨天。"))
        self.assertEqual(self.service.read_by_date("2026-10-22").body, "上午。")
        self.assertEqual(self.service.read_by_date("2026-10-22", "group-2").body, "另一个群。")

    def test_legacy_default_path_is_read_before_appending(self):
        original = self.paths.legacy_diary_file("2026-10-22")
        original.parent.mkdir(parents=True, exist_ok=True)
        original.write_text('---\ndate: "2026-10-22"\ntitle: "旧版本日记"\n---\n\n# 旧版本日记\n\n上午的旧事。\n', encoding="utf-8")
        old_bytes = original.read_bytes()
        saved = self.write("下午的新事。")
        self.assertEqual(saved.body, "上午的旧事。\n\n下午的新事。")
        self.assertEqual(original.read_bytes(), old_bytes)
        self.assertEqual(len(self.service.list_entries()), 1)

    def test_concurrent_service_instances_preserve_all_continuations(self):
        other = DiaryService(self.paths)
        self.write("上午。")
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit((self.service if i % 2 else other).write_diary, DiaryEntry(date="2026-10-22", body=f"新事件 {i}。")) for i in range(8)]
            for future in futures:
                future.result()
        saved = self.service.read_by_date("2026-10-22")
        self.assertEqual(saved.revision, 9)
        for i in range(8):
            self.assertEqual(saved.body.count(f"新事件 {i}。"), 1)
        self.assertEqual(len(list(self.paths.revision_dir_for_notebook("default", saved.date).glob("*.md"))), 8)

    def test_invalid_mode_or_empty_body_does_not_change_the_file(self):
        self.write("上午。")
        path = self.paths.diary_file_for_notebook("default", "2026-10-22")
        before = path.read_bytes()
        for mode, body in [("wrong", "新事"), ("append", "  "), ("replace", "")]:
            with self.assertRaises(ValueError):
                self.service.write_diary(DiaryEntry(date="2026-10-22", body=body), mode=mode)
        self.assertEqual(path.read_bytes(), before)

    def test_tool_client_defaults_to_append_and_accepts_explicit_replace(self):
        import asyncio
        tools = NestDiaryTools(EmbeddedNestClient(Path(self.temp.name)))
        asyncio.run(tools.write_diary(date="2026-10-22", body="上午。", title="一天"))
        result = asyncio.run(tools.write_diary(date="2026-10-22", body="下午。"))
        self.assertEqual(result["write_mode"], "append")
        self.assertEqual(self.service.read_by_date("2026-10-22").body, "上午。\n\n下午。")
        asyncio.run(tools.write_diary(date="2026-10-22", body="更正。", mode="replace"))
        self.assertEqual(self.service.read_by_date("2026-10-22").body, "更正。")

    def test_runtime_and_scheduled_rules_include_append_even_with_custom_prompt(self):
        from test_permissions_and_safety import make_plugin
        from nest_diary_web.models import ServiceUiSettings
        plugin = make_plugin(ui_settings=ServiceUiSettings(diary_write_prompt="自定义日记要求"))
        self.assertIn(DIARY_APPEND_POLICY, plugin._nest_runtime_policy_prompt())
        self.assertIn(DIARY_APPEND_POLICY, plugin._future_daily_note("default", "默认", "none", "text"))


if __name__ == "__main__":
    unittest.main()
