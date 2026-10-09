"""브라우저 백엔드 통합 테스트 (Playwright + Chromium 이 있을 때만)."""

import asyncio

import pytest

pytest.importorskip("playwright")

from vmonitor.actions import ActionRunner  # noqa: E402
from vmonitor.backends.browser import BrowserBackend  # noqa: E402

PAGE = """data:text/html,<html><body style='margin:0'>
<button id=b style='position:absolute;left:50px;top:50px;width:200px;height:60px'
  onclick='this.dataset.n=+this.dataset.n+1' data-n=0>btn</button>
<input id=t style='position:absolute;left:50px;top:150px;width:300px;height:30px'>
<div style='height:3000px'></div>
<script>document.addEventListener('dblclick',()=>document.title='dbl')</script></body></html>"""


def test_browser_end_to_end():
    async def main():
        b = BrowserBackend(url=PAGE, width=800, height=600, input_delay=0)
        try:
            await b.start()
        except Exception as e:  # 브라우저 바이너리가 없는 환경
            pytest.skip(f"Chromium 을 띄울 수 없습니다: {e}")
        try:
            img = await b.capture()
            assert img.size == (800, 600)
            r = ActionRunner(b)
            res = await r.run([
                {"action": "click", "x": 150, "y": 80},
                {"action": "double_click", "x": 150, "y": 80},
                {"action": "click", "x": 200, "y": 165},
                {"action": "type", "text": "Hi 안녕"},
                {"action": "key", "keys": "shift+a"},
                {"action": "key", "keys": "!"},
                {"action": "scroll", "x": 400, "y": 300, "dy": 2},
            ])
            assert all(x["ok"] for x in res), res
            p = b.page
            assert await p.get_attribute("#b", "data-n") == "3"
            assert await p.title() == "dbl"
            assert await p.input_value("#t") == "Hi 안녕A!"
            assert await p.evaluate("window.scrollY") == 200
            await r.run([{"action": "navigate", "url": "data:text/html,<title>two</title>"}])
            assert await p.title() == "two"
        finally:
            await b.stop()

    asyncio.run(main())
