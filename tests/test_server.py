import base64
import io

from fastapi.testclient import TestClient
from PIL import Image

from vmonitor.backends.demo import DemoBackend
from vmonitor.server import create_app


def make(token=None, read_only=False):
    backend = DemoBackend(width=960, height=600, input_delay=0)
    return backend, TestClient(create_app(backend, token=token, read_only=read_only))


def test_info_screenshot_and_actions():
    backend, c = make()
    with c:
        info = c.get("/api/info").json()
        assert info["backend"] == "demo" and info["size"] == [960, 600]

        r = c.get("/api/screenshot", params={"max_width": 480, "grid": 100})
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
        assert r.headers["x-scale"].startswith("0.5")
        assert Image.open(io.BytesIO(r.content)).size == (480, 300)

        r = c.post("/api/actions", json={"actions": [{"action": "click", "x": 50, "y": 60}],
                                         "space": [480, 300], "screenshot": {"format": "jpeg", "max_width": 320},
                                         "settle_ms": 0})
        body = r.json()
        assert body["ok"] and body["cursor"] == [100, 120]
        shot = body["screenshot"]
        assert shot["mime"] == "image/jpeg" and shot["width"] == 320
        Image.open(io.BytesIO(base64.b64decode(shot["data"])))
        assert backend.info()["buttons"]["A"] == 1

        # 단일 액션 객체도 허용
        assert c.post("/api/actions", json={"actions": {"action": "type", "text": "x"}}).json()["ok"]
        px = c.get("/api/pixel", params={"x": 5, "y": 5}).json()
        assert px["hex"].startswith("#") and len(px["rgb"]) == 3
        assert c.get("/api/pixel", params={"x": 5000, "y": 5}).status_code == 400
        assert "VMonitor" in c.get("/").text


def test_websocket_actions_and_frames():
    backend, c = make()
    with c, c.websocket_connect("/ws?frames=true&fps=20") as ws:
        ws.send_json({"id": 1, "actions": [{"action": "key", "keys": "a"}]})
        got_reply = got_frame = False
        for _ in range(20):
            msg = ws.receive()
            if msg.get("bytes"):
                assert msg["bytes"][:2] == b"\xff\xd8"  # JPEG
                got_frame = True
            elif msg.get("text"):
                import json
                reply = json.loads(msg["text"])
                assert reply["id"] == 1 and reply["ok"]
                got_reply = True
            if got_reply and got_frame:
                break
        assert got_reply and got_frame
        assert backend.state.text == "a"


def test_auth_required():
    _, c = make(token="s3cret")
    with c:
        assert c.get("/api/info").status_code == 401
        assert c.get("/api/info", headers={"Authorization": "Bearer s3cret"}).status_code == 200
        assert c.get("/api/info", params={"token": "s3cret"}).status_code == 200
        assert c.get("/api/info", headers={"X-API-Key": "wrong"}).status_code == 401
        assert c.get("/").status_code == 200  # 뷰어 HTML 자체는 공개 (데이터는 토큰 필요)


def test_view_only():
    _, c = make(read_only=True)
    with c:
        r = c.post("/api/actions", json={"actions": [{"action": "click", "x": 1, "y": 1}]}).json()
        assert not r["ok"]
        assert c.get("/api/screenshot").status_code == 200
