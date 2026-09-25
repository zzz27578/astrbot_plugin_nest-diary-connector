import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PluginPageTest(unittest.TestCase):
    def test_plugin_page_loads_the_real_webui_bundle(self) -> None:
        index = (ROOT / "pages" / "nest" / "index.html").read_text(encoding="utf-8")
        page_script = ROOT / "pages" / "nest" / "assets" / "app.js"
        webui_script = ROOT / "nest_diary_web" / "web_dist" / "assets" / "app.js"
        page_css = ROOT / "pages" / "nest" / "assets" / "app.css"
        webui_css = ROOT / "nest_diary_web" / "web_dist" / "assets" / "app.css"

        self.assertIn("/api/plugin/page/bridge-sdk.js", index)
        self.assertIn('src="./assets/app.js?ui=0.6.0"', index)
        self.assertIn('href="./assets/app.css?ui=0.6.0"', index)
        self.assertEqual(page_script.read_bytes(), webui_script.read_bytes())
        self.assertEqual(page_css.read_bytes(), webui_css.read_bytes())

    def test_webui_bundle_uses_bridge_transport_inside_plugin_page(self) -> None:
        script = (ROOT / "pages" / "nest" / "assets" / "app.js").read_text(encoding="utf-8")

        self.assertIn("PLUGIN_PAGE_BRIDGE", script)
        self.assertIn('apiPost("ui/proxy"', script)
        self.assertIn('upload("ui/upload/avatar"', script)
        self.assertIn('download("ui/export"', script)
        self.assertIn("confirmAction", script)
        self.assertIn("normalizePluginBridgeResult", script)
        self.assertIn("new URL(import.meta.url)", script)
        self.assertIn("PLUGIN_PAGE_MODULE_URL.searchParams.forEach", script)
        self.assertIn('apiGet("ui/avatar")', script)
        self.assertIn("function importModulePage(", script)
        self.assertIn("pluginApi(entry.page_url)", script)

    def test_plugin_page_backend_routes_are_namespaced(self) -> None:
        source = (ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn('f"/{PLUGIN_NAME}/ui/proxy"', source)
        self.assertIn('f"/{PLUGIN_NAME}/ui/upload/<upload_kind>"', source)
        self.assertIn('f"/{PLUGIN_NAME}/ui/avatar"', source)
        self.assertIn("_plugin_page_avatar_data_url", source)
        self.assertIn('f"/{PLUGIN_NAME}/ui/export"', source)
        self.assertIn('f"/{PLUGIN_NAME}/ui/media"', source)
        self.assertIn("_allowed_plugin_page_path", source)
        self.assertEqual(source.count('make_json_response({"data": result})'), 3)

    def test_astrbot_bridge_unwrap_keeps_proxy_envelope(self) -> None:
        proxy_result = {
            "ok": True,
            "data": {"version": "0.6.0"},
            "web_host": "0.0.0.0",
            "web_port": 28080,
        }
        handler_response = {"data": proxy_result}
        bridge_value = handler_response.get("data", handler_response)

        self.assertTrue(bridge_value["ok"])
        self.assertEqual(bridge_value["data"]["version"], "0.6.0")
        self.assertEqual(bridge_value["web_port"], 28080)

    def test_proxy_path_allowlist_blocks_external_urls(self) -> None:
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('or "://" in clean_path', source)
        self.assertIn('base_path == "/theme.css"', source)
        self.assertIn('base_path.startswith("/api/ui/")', source)


if __name__ == "__main__":
    unittest.main()
