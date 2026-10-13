"""페이지 데이터 읽기: 플레이어 목록 해석, 응답 감시, stealth, 헤더, 스크립트 실행."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastapi.testclient import TestClient

from vmonitor.backends.browser import BrowserBackend, find_players
from vmonitor.backends.demo import DemoBackend
from vmonitor.server import create_app

DYNMAP = {"timestamp": 1, "players": [
    {"world": "world", "armor": 0, "name": "Steve", "x": 10.5, "y": 64.0, "health": 20, "z": -3.0,
     "sort": 0, "type": "player", "account": "Steve"}]}
BLUEMAP = {"players": [{"uuid": "u-1", "name": "Alex", "foreign": False,
                        "position": {"x": 1.0, "y": 70.0, "z": 2.0}, "rotation": {"pitch": 0}}]}


def test_find_players_formats():
    p = find_players(DYNMAP)[0]
    assert (p["name"], p["world"], p["x"], p["y"], p["z"], p["health"]) == ("Steve", "world", 10.5, 64.0, -3.0, 20)
    p = find_players(BLUEMAP)[0]
    assert (p["name"], p["account"], p["x"], p["y"], p["z"]) == ("Alex", "u-1", 1.0, 70.0, 2.0)
    assert find_players({"data": {"players": []}}) == []  # 한 단계 안쪽 + 0명
    assert find_players({"tiles": []}) is None
    assert find_players([1, 2]) is None


def test_page_data_endpoints_unsupported_backend():
    with TestClient(create_app(DemoBackend(input_delay=0))) as c:
        assert c.get("/api/players").status_code == 400
        assert c.get("/api/captures").status_code == 400
        assert c.post("/api/eval", json={"expression": "1"}).status_code == 400
    with TestClient(create_app(DemoBackend(input_delay=0), read_only=True)) as c:
        assert c.post("/api/eval", json={"expression": "1"}).status_code == 403


PAGE = b"""<html><body><script>
fetch('/up/world/world/123').then(r => r.json());
fetch('/other.json').then(r => r.json());
</script></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    seen_headers: list[dict[str, str]] = []

    def do_GET(self):  # noqa: N802
        _Handler.seen_headers.append(dict(self.headers))
        if self.path == "/":
            body, ctype = PAGE, "text/html"
        elif self.path.startswith("/up/"):
            body, ctype = json.dumps(DYNMAP).encode(), "application/json"
        else:
            body, ctype = b'{"hello": 1}', "application/json"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_browser_watch_players_stealth_eval(unavailable):
    import pytest

    pytest.importorskip("playwright")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"

    async def main():
        b = BrowserBackend(url=None, width=640, height=480, input_delay=0, stealth=True, user_agent="VMonitorTest/1",
                           extra_headers={"X-Allow-Token": "abc"}, watch=[r"/up/"])
        try:
            await b.start()
        except Exception as e:
            unavailable(f"Chromium 을 띄울 수 없습니다: {e}")
        try:
            await b.navigate(url)
            for _ in range(50):
                if b.players()["count"]:
                    break
                await asyncio.sleep(0.1)
            r = b.players()
            assert r["count"] == 1 and r["players"][0]["name"] == "Steve" and r["source"].endswith("/up/world/world/123")
            # watch 패턴에 맞지 않는 응답은 기록하지 않음
            assert [c["url"] for c in b.captures()] == [r["source"]]
            assert await b.evaluate("navigator.webdriver") is None
            assert await b.evaluate("Promise.resolve(1 + 2)") == 3
            hdr = {k.lower(): v for k, v in _Handler.seen_headers[-1].items()}
            assert hdr["x-allow-token"] == "abc" and hdr["user-agent"] == "VMonitorTest/1"
        finally:
            await b.stop()
            httpd.shutdown()

    asyncio.run(main())
