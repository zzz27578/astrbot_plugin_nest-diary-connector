"""自定义模块契约测试。

覆盖 0.6.0 的三件事：能力声明解析、模块隔离存储、以及前端确实按清单渲染侧边栏。
不依赖 fastapi，可以在裸环境跑。
"""

import json
import tempfile
import unittest
from pathlib import Path

from nest_diary_web.module_registry import (
    BUILTIN_NAV_ICONS,
    OFFICIAL_APPEARANCE_IDS,
    OFFICIAL_MODULE_IDS,
    RESERVED_MODULE_DIR_NAMES,
    STORE_MAX_KEYS,
    collect_nav_entries,
    is_official_id,
    module_asset_base,
    module_route,
    nav_entry_for,
    normalize_capabilities,
    resolve_asset,
    verify_page_entry,
)
from nest_diary_web.module_store import ModuleStore, ModuleStoreError
from nest_diary_web.paths import NestPaths

ROOT = Path(__file__).resolve().parents[1]


class CapabilityDeclarationTest(unittest.TestCase):
    def test_full_declaration_is_normalized(self) -> None:
        capabilities = normalize_capabilities(
            {
                "id": "habit-board",
                "name": "习惯打卡",
                "runtime": "webui",
                "nav": {"label": "打卡", "icon": "modules", "order": 310},
                "page": {"entry": "page.js", "export": "mount"},
                "store": True,
            }
        )

        self.assertEqual(capabilities["errors"], [])
        self.assertEqual(capabilities["runtime"], "webui")
        self.assertEqual(capabilities["nav"]["label"], "打卡")
        self.assertEqual(capabilities["nav"]["order"], 310)
        self.assertEqual(capabilities["nav"]["route"], "/m/habit-board")
        self.assertEqual(capabilities["page"]["entry"], "page.js")
        self.assertEqual(capabilities["page"]["export"], "mount")
        self.assertTrue(capabilities["store"])

    def test_tool_only_module_gets_no_nav_and_no_page(self) -> None:
        """只提供工具的模块不该被迫顶着空入口和空页面。"""
        capabilities = normalize_capabilities({"id": "quiet-tools", "name": "工具包", "tools": ["do_thing"]})

        self.assertIsNone(capabilities["nav"])
        self.assertIsNone(capabilities["page"])
        self.assertIsNone(capabilities["store"])
        self.assertEqual(capabilities["errors"], [])

    def test_nav_without_page_is_rejected(self) -> None:
        capabilities = normalize_capabilities({"id": "broken", "name": "坏的", "nav": True})

        self.assertIsNone(capabilities["nav"])
        self.assertTrue(any("page" in message for message in capabilities["errors"]))

    def test_page_entry_rejects_traversal_and_absolute_paths(self) -> None:
        for entry in ["../../etc/passwd", "/etc/passwd", ".."]:
            capabilities = normalize_capabilities({"id": "evil", "name": "evil", "page": entry})
            self.assertIsNone(capabilities["page"], entry)

    def test_omitted_entry_defaults_to_page_js(self) -> None:
        capabilities = normalize_capabilities({"id": "x", "name": "x", "page": True})

        self.assertEqual(capabilities["page"]["entry"], "page.js")

    def test_page_entry_must_be_a_script(self) -> None:
        capabilities = normalize_capabilities({"id": "x", "name": "x", "page": "index.html"})

        self.assertIsNone(capabilities["page"])
        self.assertTrue(any(".mjs" in message for message in capabilities["errors"]))

    def test_unknown_runtime_falls_back_to_webui(self) -> None:
        capabilities = normalize_capabilities({"id": "x", "name": "x", "runtime": "rust"})

        self.assertEqual(capabilities["runtime"], "webui")
        self.assertTrue(capabilities["errors"])

    def test_custom_nav_icon_must_be_a_relative_asset(self) -> None:
        capabilities = normalize_capabilities(
            {"id": "x", "name": "x", "page": "page.js", "nav": {"icon": "icons/mine.svg"}}
        )

        self.assertEqual(capabilities["nav"]["icon"], "")
        self.assertEqual(capabilities["nav"]["icon_asset"], "icons/mine.svg")

    def test_nav_order_is_clamped(self) -> None:
        low = normalize_capabilities({"id": "a", "name": "a", "page": "page.js", "nav": {"order": -50}})
        high = normalize_capabilities({"id": "b", "name": "b", "page": "page.js", "nav": {"order": 99999}})

        self.assertEqual(low["nav"]["order"], 0)
        self.assertEqual(high["nav"]["order"], 999)

    def test_explicitly_disabled_capabilities_are_dropped(self) -> None:
        capabilities = normalize_capabilities(
            {
                "id": "x",
                "name": "x",
                "page": {"entry": "page.js", "enabled": False},
                "nav": {"enabled": False},
                "store": {"enabled": False},
            }
        )

        self.assertIsNone(capabilities["page"])
        self.assertIsNone(capabilities["nav"])
        self.assertIsNone(capabilities["store"])


class OfficialRegistryTest(unittest.TestCase):
    def test_official_ids_are_protected(self) -> None:
        for module_id in OFFICIAL_MODULE_IDS:
            self.assertTrue(is_official_id(module_id))
        for appearance_id in OFFICIAL_APPEARANCE_IDS:
            self.assertTrue(is_official_id(appearance_id))
        self.assertFalse(is_official_id("habit-board"))

    def test_reserved_dir_names_cover_official_modules_and_containers(self) -> None:
        for module_id in OFFICIAL_MODULE_IDS:
            self.assertIn(module_id, RESERVED_MODULE_DIR_NAMES)
        self.assertIn("extensions", RESERVED_MODULE_DIR_NAMES)
        self.assertIn("archive", RESERVED_MODULE_DIR_NAMES)

    def test_official_module_dirs_match_the_registry(self) -> None:
        for module_id in OFFICIAL_MODULE_IDS:
            manifest = ROOT / "modules" / module_id / "module.json"
            self.assertTrue(manifest.is_file(), module_id)
            self.assertEqual(json.loads(manifest.read_text(encoding="utf-8"))["id"], module_id)

    def test_builtin_nav_icons_have_real_svg_files(self) -> None:
        icons_dir = ROOT / "nest_diary_web" / "web_dist" / "assets" / "icons"
        for icon in BUILTIN_NAV_ICONS:
            self.assertTrue((icons_dir / f"{icon}.svg").is_file(), icon)

    def test_route_and_asset_base_are_namespaced(self) -> None:
        self.assertEqual(module_route("habit-board"), "/m/habit-board")
        self.assertEqual(module_asset_base("habit-board"), "/api/ui/module-assets/habit-board")


class AssetResolutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.module_dir = Path(self.tmp.name) / "habit-board"
        self.module_dir.mkdir(parents=True)
        (self.module_dir / "page.js").write_text("export function mount() {}", encoding="utf-8")
        (Path(self.tmp.name) / "secret.txt").write_text("nope", encoding="utf-8")
        self.manifest = {"id": "habit-board", "name": "x", "data_path": str(self.module_dir)}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_resolves_declared_entry(self) -> None:
        self.assertIsNotNone(resolve_asset(self.manifest, "page.js"))

    def test_rejects_escape_from_module_root(self) -> None:
        self.assertIsNone(resolve_asset(self.manifest, "../secret.txt"))

    def test_rejects_disallowed_suffix(self) -> None:
        (self.module_dir / "run.sh").write_text("echo hi", encoding="utf-8")
        self.assertIsNone(resolve_asset(self.manifest, "run.sh"))

    def test_verify_page_entry_reports_missing_file(self) -> None:
        manifest = dict(self.manifest)
        manifest["capabilities"] = normalize_capabilities({"id": "habit-board", "name": "x", "page": "missing.js"})
        self.assertNotEqual(verify_page_entry(manifest), "")

    def test_verify_page_entry_passes_for_real_file(self) -> None:
        manifest = dict(self.manifest)
        manifest["capabilities"] = normalize_capabilities({"id": "habit-board", "name": "x", "page": "page.js"})
        self.assertEqual(verify_page_entry(manifest), "")

    def test_webui_subdirectory_is_searched(self) -> None:
        nested = self.module_dir / "webui"
        nested.mkdir()
        (nested / "inner.js").write_text("export function mount() {}", encoding="utf-8")
        self.assertIsNotNone(resolve_asset(self.manifest, "inner.js"))


class ModuleStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = NestPaths(Path(self.tmp.name))
        self.store = ModuleStore(self.paths)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_write_read_roundtrip(self) -> None:
        self.store.write("habit-board", "checkins", ["2026-08-04"])
        result = self.store.read("habit-board", "checkins")

        self.assertTrue(result["exists"])
        self.assertEqual(result["value"], ["2026-08-04"])

    def test_data_lands_in_module_data_dir(self) -> None:
        self.store.write("habit-board", "checkins", [1])
        expected = self.paths.modules_dir / "habit-board" / "data" / "store" / "checkins.json"

        self.assertTrue(expected.is_file())

    def test_missing_key_reports_absent_without_raising(self) -> None:
        result = self.store.read("habit-board", "nothing")

        self.assertFalse(result["exists"])
        self.assertIsNone(result["value"])

    def test_modules_cannot_reach_each_other(self) -> None:
        self.store.write("module-a", "shared", "a")
        self.store.write("module-b", "shared", "b")

        self.assertEqual(self.store.read("module-a", "shared")["value"], "a")
        self.assertEqual(self.store.read("module-b", "shared")["value"], "b")

    def test_unsafe_keys_are_rejected(self) -> None:
        for key in ["../escape", "with/slash", "", "  "]:
            with self.assertRaises(ModuleStoreError, msg=key):
                self.store.write("habit-board", key, 1)

    def test_unsafe_module_id_is_rejected(self) -> None:
        with self.assertRaises(ModuleStoreError):
            self.store.write("../escape", "key", 1)

    def test_oversized_document_is_rejected(self) -> None:
        with self.assertRaises(ModuleStoreError) as caught:
            self.store.write("habit-board", "big", "x" * 5000, max_bytes=1024)

        self.assertEqual(caught.exception.status_code, 413)

    def test_key_count_is_capped(self) -> None:
        for index in range(STORE_MAX_KEYS):
            self.store.write("habit-board", f"key{index}", index)

        with self.assertRaises(ModuleStoreError) as caught:
            self.store.write("habit-board", "one-too-many", 1)

        self.assertEqual(caught.exception.status_code, 409)

    def test_overwriting_an_existing_key_at_the_cap_still_works(self) -> None:
        for index in range(STORE_MAX_KEYS):
            self.store.write("habit-board", f"key{index}", index)

        self.store.write("habit-board", "key0", "updated")
        self.assertEqual(self.store.read("habit-board", "key0")["value"], "updated")

    def test_non_serializable_value_is_rejected(self) -> None:
        with self.assertRaises(ModuleStoreError):
            self.store.write("habit-board", "bad", {1, 2, 3})

    def test_delete_is_idempotent(self) -> None:
        self.store.write("habit-board", "gone", 1)

        self.assertTrue(self.store.delete("habit-board", "gone")["deleted"])
        self.assertFalse(self.store.delete("habit-board", "gone")["deleted"])

    def test_list_keys_reports_sizes(self) -> None:
        self.store.write("habit-board", "one", [1, 2, 3])
        keys = self.store.list_keys("habit-board")

        self.assertEqual([item["key"] for item in keys], ["one"])
        self.assertGreater(keys[0]["bytes"], 0)


class ThreeLayerGatingTest(unittest.TestCase):
    """入口要同时过三层：模块声明、用户启用、框架验真。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.module_dir = Path(self.tmp.name) / "habit-board"
        self.module_dir.mkdir(parents=True)
        (self.module_dir / "page.js").write_text("export function mount() {}", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def manifest(self, **overrides) -> dict:
        raw = {
            "id": "habit-board",
            "name": "习惯打卡",
            "page": "page.js",
            "nav": {"label": "打卡", "order": 310},
        }
        raw.update(overrides)
        item = dict(raw)
        item["asset_base"] = module_asset_base(item["id"])
        item["capabilities"] = normalize_capabilities(raw)
        item["capabilities"].pop("errors", None)
        item["data_path"] = str(self.module_dir)
        return item

    def test_declared_enabled_and_verified_module_gets_an_entry(self) -> None:
        entries = collect_nav_entries([([self.manifest()], ["habit-board"])])

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["label"], "打卡")
        self.assertEqual(entries[0]["route"], "/m/habit-board")
        self.assertEqual(entries[0]["page_url"], "/api/ui/module-assets/habit-board/page.js")

    def test_layer_one_undeclared_nav_gets_no_entry(self) -> None:
        item = self.manifest(nav=None)

        self.assertEqual(collect_nav_entries([([item], ["habit-board"])]), [])

    def test_layer_two_disabled_module_gets_no_entry(self) -> None:
        self.assertEqual(collect_nav_entries([([self.manifest()], [])]), [])

    def test_layer_two_hidden_entry_is_withheld_while_module_stays_enabled(self) -> None:
        entries = collect_nav_entries([([self.manifest()], ["habit-board"])], hidden_ids=["habit-board"])

        self.assertEqual(entries, [])

    def test_layer_three_missing_page_file_withholds_the_entry(self) -> None:
        item = self.manifest(page="gone.js")
        # 模拟发现阶段的验真：文件不存在就把 nav 一起收回。
        if verify_page_entry(item):
            item["capabilities"]["page"] = None
            item["capabilities"]["nav"] = None

        self.assertEqual(collect_nav_entries([([item], ["habit-board"])]), [])

    def test_entries_are_sorted_by_order_then_id(self) -> None:
        late = self.manifest(id="z-late", nav={"label": "晚", "order": 700})
        early = self.manifest(id="a-early", nav={"label": "早", "order": 100})
        entries = collect_nav_entries([([late, early], ["z-late", "a-early"])])

        self.assertEqual([entry["id"] for entry in entries], ["a-early", "z-late"])

    def test_custom_nav_icon_becomes_a_namespaced_url(self) -> None:
        icons = self.module_dir / "icons"
        icons.mkdir()
        (icons / "mine.svg").write_text("<svg/>", encoding="utf-8")
        item = self.manifest(nav={"label": "打卡", "icon": "icons/mine.svg"})
        entry = nav_entry_for(item)

        self.assertEqual(entry["icon_url"], "/api/ui/module-assets/habit-board/icons/mine.svg")

    def test_nav_entry_for_returns_none_without_page(self) -> None:
        self.assertIsNone(nav_entry_for({"id": "x", "capabilities": {"nav": {"label": "x"}, "page": None}}))


class FrontendContractTest(unittest.TestCase):
    """侧边栏必须由清单驱动，模块页面必须真的被 import。"""

    def setUp(self) -> None:
        self.script = (ROOT / "pages" / "nest" / "assets" / "app.js").read_text(encoding="utf-8")

    def test_sidebar_merges_official_and_custom_entries(self) -> None:
        self.assertIn("function customNavLinks()", self.script)
        self.assertIn("function officialNavLinks()", self.script)
        self.assertIn("[...officialNavLinks(), ...customNavLinks()]", self.script)

    def test_custom_entries_come_from_the_catalog(self) -> None:
        self.assertIn("catalog.nav_entries", self.script)

    def test_module_pages_are_dynamically_imported_and_mounted(self) -> None:
        self.assertIn("function importModulePage(", self.script)
        self.assertIn("PLUGIN_PAGE_BRIDGE", self.script)
        self.assertIn("pluginApi(entry.page_url)", self.script)
        self.assertIn("URL.createObjectURL", self.script)
        self.assertIn("await import(", self.script)
        self.assertIn("entry.page_url", self.script)
        self.assertIn("mount(target, context)", self.script)

    def test_module_route_and_panel_are_dynamic(self) -> None:
        self.assertIn("function ensureModulePanel(", self.script)
        self.assertIn('`/m/${encodeURIComponent(moduleIdFromView(view))}`', self.script)
        self.assertIn('path.startsWith("/m/")', self.script)

    def test_module_context_exposes_the_documented_capabilities(self) -> None:
        self.assertIn("function moduleContext(", self.script)
        for member in ["store:", "assetUrl(", "notify(", "reportError(", "confirm(", "request("]:
            self.assertIn(member, self.script, member)

    def test_mount_failure_is_contained(self) -> None:
        self.assertIn("function moduleFailure(", self.script)
        self.assertIn("function unmountModulePage(", self.script)
        self.assertIn("function pruneModuleMounts(", self.script)

    def test_uninstall_and_nav_visibility_are_wired(self) -> None:
        delegated_selector = next(
            line for line in self.script.splitlines() if "const target = event.target.closest" in line
        )
        self.assertIn("[data-module-uninstall]", delegated_selector)
        self.assertIn("void uninstallModule(target.dataset.moduleUninstall", self.script)
        self.assertIn("/api/ui/modules/uninstall", self.script)
        self.assertIn("saveModuleNavVisibility", self.script)
        self.assertIn("hidden_module_nav_ids", self.script)

    def test_plugin_page_avoids_direct_module_icon_requests(self) -> None:
        """插件页没有独立 WebUI 的 Cookie，模块图标要退回内置图标。"""
        self.assertIn("PLUGIN_PAGE_BRIDGE ? \"\" : entry.icon_url", self.script)


class ExampleModuleTest(unittest.TestCase):
    """随插件分发的示例模块必须符合它自己描述的契约。"""

    def setUp(self) -> None:
        self.example = ROOT / "examples" / "habit-board"

    def test_manifest_declares_the_lightweight_contract(self) -> None:
        manifest = json.loads((self.example / "module.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["runtime"], "webui")
        self.assertEqual(manifest["page"]["entry"], "page.js")
        self.assertTrue(manifest["store"])
        self.assertFalse(is_official_id(manifest["id"]))

    def test_manifest_passes_capability_validation(self) -> None:
        manifest = json.loads((self.example / "module.json").read_text(encoding="utf-8"))
        capabilities = normalize_capabilities(manifest)

        self.assertEqual(capabilities["errors"], [])
        self.assertIsNotNone(capabilities["nav"])
        self.assertIsNotNone(capabilities["page"])

    def test_declared_entry_exists_and_exports_mount(self) -> None:
        manifest = dict(json.loads((self.example / "module.json").read_text(encoding="utf-8")))
        manifest["data_path"] = str(self.example)
        manifest["capabilities"] = normalize_capabilities(manifest)

        self.assertEqual(verify_page_entry(manifest), "")
        self.assertIn("export async function mount(", (self.example / "page.js").read_text(encoding="utf-8"))

    def test_example_uses_framework_storage_not_localstorage(self) -> None:
        page = (self.example / "page.js").read_text(encoding="utf-8")

        self.assertIn("ctx.store", page)
        self.assertNotIn("localStorage", page)


if __name__ == "__main__":
    unittest.main()
