"""X11 백엔드 통합 테스트 (Linux + Xvfb + python-xlib 이 있을 때만)."""

import asyncio
import shutil
import sys

import pytest

if sys.platform != "linux" or not shutil.which("Xvfb"):
    pytest.skip("Xvfb 가 필요합니다", allow_module_level=True)
pytest.importorskip("Xlib")

from vmonitor.actions import ActionRunner  # noqa: E402
from vmonitor.backends.x11 import X11Backend, char_keysym  # noqa: E402


def test_char_keysym():
    assert char_keysym("a") == ord("a")
    assert char_keysym("한") == 0x01000000 | ord("한")
    assert char_keysym("\n") == 0xFF0D


def test_virtual_monitor_capture_and_pointer():
    async def main():
        b = X11Backend(xvfb=True, width=640, height=480, input_delay=0)
        await b.start()
        try:
            img = await b.capture()
            assert img.size == (640, 480)
            r = ActionRunner(b)
            res = await r.run([{"action": "move", "x": 123, "y": 45}, {"action": "click"},
                               {"action": "type", "text": "aB한"}, {"action": "key", "keys": "ctrl+shift+t"}])
            assert all(x["ok"] for x in res), res
            ptr = b._d.screen().root.query_pointer()
            assert (ptr.root_x, ptr.root_y) == (123, 45)
        finally:
            await b.stop()

    asyncio.run(main())


def test_app_window_typing():
    """Xvfb 안에 Chromium 앱 창을 띄워 창 영역만 송출하고, 클릭·한글 타이핑이 실제로 들어가는지 확인."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            chrome = pw.chromium.executable_path
    except Exception:
        pytest.skip("Chromium 이 없습니다")
    import os
    import tempfile

    tmp = tempfile.mkdtemp()
    page = os.path.join(tmp, "p.html")
    with open(page, "w", encoding="utf-8") as f:
        f.write("<title>start</title><body style='margin:0'>"
                "<input id=t style='position:absolute;left:0;top:0;width:100%;height:100%;font-size:40px'"
                " oninput=\"document.title='v='+this.value\"></body>")
    cmd = (f"{chrome} --no-sandbox --test-type --no-first-run --disable-gpu --user-data-dir={tmp}/prof "
           f"--window-position=0,0 --window-size=600,400 --app=file://{page}")

    async def main():
        b = X11Backend(xvfb=True, width=800, height=600, launch=cmd, window="start|v=", input_delay=0)
        try:
            await b.start()
        except Exception as e:
            pytest.skip(f"앱 창을 띄울 수 없습니다: {e}")
        try:
            await asyncio.sleep(2)
            img = await b.capture()
            w, h = img.size
            r = ActionRunner(b)
            res = await r.run([{"action": "click", "x": w // 2, "y": h - 40}, {"action": "type", "text": "ok 한글"},
                               {"action": "key", "keys": "shift+a"}])
            assert all(x["ok"] for x in res), res
            for _ in range(20):
                await asyncio.sleep(0.25)
                if b.info().get("window_title") == "v=ok 한글A":
                    break
            assert b.info().get("window_title") == "v=ok 한글A"
        finally:
            await b.stop()

    asyncio.run(main())
