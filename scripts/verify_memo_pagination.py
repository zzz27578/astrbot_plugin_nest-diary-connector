"""Optional browser regression check; requires Playwright and a Chromium installation.

Run: python scripts/verify_memo_pagination.py --output-dir <screenshots-directory>
All test data lives in a temporary directory. No running AstrBot instance is used.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--chromium", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from playwright.sync_api import sync_playwright
    import uvicorn

    with tempfile.TemporaryDirectory(prefix="nest-memo-ui-") as temp:
        os.environ["NEST_DATA_DIR"] = temp
        from nest_diary_web import main as backend

        settings = backend.service_settings.load()
        settings.onboarding_completed = True
        settings.appearance_modules_initialized = True
        settings.active_frontend_style = "default"
        settings.enabled_appearance_modules = []
        backend.service_settings.save(settings)
        for i in range(27):
            backend.memo_service.create(title=f"纸条 {i + 1}：把要记的事安放好", content=f"这是第 {i + 1} 张旧纸条。翻页后仍然可以完整读取。" + "很长的内容。" * 20, tags=["待办", "旧版本", "很长的标签" * 6], pinned=i == 0, sensitive=i == 1)
        backend.memo_service.create(title="已归档", content="归档内容也保留。", archived=True)
        before = backend.memo_service.path.read_bytes()
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(backend.app, log_level="error"))
        thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        thread.start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started, "Preview server did not start"
        origin = f"http://127.0.0.1:{port}"
        results = []
        try:
            with sync_playwright() as playwright:
                executable = args.chromium
                if executable is None and os.name == "nt":
                    installed = sorted((Path(os.environ["LOCALAPPDATA"]) / "ms-playwright").glob("chromium-*/chrome-win64/chrome.exe"))
                    executable = installed[-1] if installed else None
                browser = playwright.chromium.launch(headless=True, **({"executable_path": str(executable)} if executable else {}))
                context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
                context.add_cookies([{"name": "nest_session", "value": backend.web_auth.create_session_token(), "url": origin}])
                page = context.new_page()
                page.on("dialog", lambda dialog: dialog.accept())
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                security = backend.security_settings.load()
                security.bot_api_token = "local-pagination-test-token"
                security.external_api_enabled = True
                backend.security_settings.save(security)
                api_headers = {"Authorization": "Bearer local-pagination-test-token"}
                assert context.request.post(origin + "/api/v1/diary/write", headers=api_headers, data={"date": "2026-10-22", "title": "一天", "body": "上午的事。"}).status == 200
                assert context.request.post(origin + "/api/v1/diary/write", headers=api_headers, data={"date": "2026-10-22", "body": "下午的事。"}).status == 200
                assert backend.diary_service.read_by_date("2026-10-22").body == "上午的事。\n\n下午的事。"
                assert context.request.post(origin + "/api/v1/diary/write", headers=api_headers, data={"date": "2026-10-22", "body": "x", "mode": "invalid"}).status == 422
                assert context.request.post(origin + "/api/ui/diary", data={"date": "2026-10-22", "title": "网页更正", "body": "更正后的完整日记。"}).status == 200
                assert backend.diary_service.read_by_date("2026-10-22").body == "更正后的完整日记。"

                def open_board():
                    page.goto(origin + "/memos")
                    page.locator(".memo-pagination").wait_for()
                    page.wait_for_timeout(200)
                    assert not errors, errors

                def inspect_layout(name):
                    geometry = page.evaluate("""() => {
                        const board = document.querySelector('.memo-board').getBoundingClientRect();
                        const grid = document.querySelector('.memo-note-grid');
                        const pager = document.querySelector('.memo-pagination').getBoundingClientRect();
                        return {capacity: document.querySelectorAll('.memo-note').length, label: document.querySelector('.memo-page-label').innerText,
                            viewport: innerHeight, pageHeight: document.documentElement.scrollHeight, pageWidth: document.documentElement.scrollWidth,
                            pagerBottom: pager.bottom, boardBottom: board.bottom, gridHeight: grid.clientHeight, gridScrollHeight: grid.scrollHeight,
                            clipped: [...document.querySelectorAll('.memo-note')].some(n => { const r=n.getBoundingClientRect(); return r.bottom>board.bottom-6 || r.right>board.right-6 || r.left<board.left+6; })};
                    }""")
                    page.screenshot(path=str(args.output_dir / f"{name}.png"), full_page=True)
                    assert not geometry["clipped"], geometry
                    assert geometry["gridScrollHeight"] <= geometry["gridHeight"] + 1, geometry
                    assert geometry["pageWidth"] <= page.viewport_size["width"], geometry
                    if page.viewport_size["height"] >= 650:
                        assert geometry["pagerBottom"] <= geometry["viewport"], geometry
                    results.append({"name": name, **geometry})

                open_board()
                assert page.get_by_role("button", name="上一页", exact=True).is_disabled()
                seen = []
                while True:
                    seen.extend(page.locator(".memo-note").evaluate_all("nodes => nodes.map(n => n.dataset.memoId)"))
                    button = page.get_by_role("button", name="下一页", exact=True)
                    if button.is_disabled():
                        break
                    button.click()
                    page.wait_for_timeout(50)
                assert len(seen) == len(set(seen)) == 27, seen
                page.get_by_role("button", name="上一页", exact=True).click()
                assert backend.memo_service.path.read_bytes() == before
                open_board()
                inspect_layout("memos-desktop")
                # Huge offsets from an old browser are bounded visually without changing storage.
                placements = {seen[0]: {"x": 9000, "y": 9000, "rot": 2}}
                page.evaluate("p => localStorage.setItem('nestMemoBoardPlacements', JSON.stringify(p))", placements)
                open_board()
                inspect_layout("memos-legacy-placement")
                assert json.loads(page.evaluate("localStorage.getItem('nestMemoBoardPlacements')")) == placements
                page.evaluate("localStorage.removeItem('nestMemoBoardPlacements')")

                for name, width, height in [("memos-phone", 375, 812), ("memos-phone-small", 375, 667), ("memos-landscape", 812, 375), ("memos-tablet", 1024, 768)]:
                    page.set_viewport_size({"width": width, "height": height})
                    open_board()
                    inspect_layout(name)

                page.set_viewport_size({"width": 1440, "height": 900})
                for theme in ["nest-paper-garden", "nest-glass-cabin", "nest-night-atelier"]:
                    settings.active_frontend_style = theme
                    settings.enabled_appearance_modules = [theme]
                    backend.service_settings.save(settings)
                    open_board()
                    inspect_layout(theme)

                # A memo opened on page two returns to that page after closing.
                page.get_by_role("button", name="下一页", exact=True).click()
                old_label = page.locator(".memo-page-label").inner_text()
                first_note = page.locator(".memo-note-open").first
                first_note.click()
                page.locator(".memo-form").wait_for()
                page.get_by_role("button", name="返回板面", exact=True).click()
                page.locator(".memo-pagination").wait_for()
                page.wait_for_timeout(200)
                assert page.locator(".memo-page-label").inner_text() == old_label, {"before": old_label, "after": page.locator(".memo-page-label").inner_text()}
                # Editing still updates a full memo, without losing unrelated notes.
                page.locator(".memo-note-open").first.click()
                form = page.locator(".memo-form")
                memo_id = form.locator('[name="id"]').input_value()
                form.locator('[name="content"]').fill("编辑后完整内容。")
                page.get_by_role("button", name="保存纸条并退出", exact=True).click()
                page.locator(".memo-pagination").wait_for()
                assert backend.memo_service.get(memo_id).content == "编辑后完整内容。"
                assert len(backend.memo_service.list_memos(include_archived=True)) == 28
                # Pin/archive actions still address the exact card on the current page.
                note = page.locator(".memo-note").first
                flag_id = note.get_attribute("data-memo-id")
                pinned = backend.memo_service.get(flag_id).pinned
                note.locator("[data-memo-pin]").click()
                page.wait_for_timeout(200)
                assert backend.memo_service.get(flag_id).pinned != pinned

                # Archived and sensitive controls continue to use the original APIs.
                page.get_by_role("button", name="显示归档", exact=True).click()
                page.wait_for_timeout(200)
                assert "28 张纸条" in page.locator(".memo-page-label").inner_text()
                page.get_by_role("button", name="隐藏归档", exact=True).click()
                page.get_by_role("button", name="显示敏感", exact=True).click()
                page.wait_for_timeout(200)
                assert "隐藏敏感" in page.locator(".memos-topbar").inner_text()
                # Shrinking the last page and emptying the board clamps the page safely.
                last_id = backend.memo_service.list_memos()[0].id
                for item in backend.memo_service.list_memos(include_archived=True):
                    if item.id != last_id:
                        backend.memo_service.delete(item.id)
                open_board()
                assert page.locator(".memo-note").count() == 1
                assert page.get_by_role("button", name="上一页", exact=True).is_disabled()
                assert page.get_by_role("button", name="下一页", exact=True).is_disabled()
                page.locator(".memo-note-open").click()
                page.get_by_role("button", name="删除", exact=True).click()
                page.locator(".memo-pagination").wait_for()
                page.wait_for_timeout(200)
                assert "第 1 / 1 页" in page.locator(".memo-page-label").inner_text()
                assert "0 张纸条" in page.locator(".memo-page-label").inner_text()
                assert page.locator(".memo-empty").is_visible()
                assert not errors, errors
                browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=10)
        (args.output_dir / "browser-results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": "ok", "layouts": len(results), "unique_memos": len(seen), "results": str(args.output_dir)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
