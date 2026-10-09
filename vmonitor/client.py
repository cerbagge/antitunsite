"""매크로·AI 에서 쓰는 파이썬 클라이언트.

    from vmonitor.client import MonitorClient

    m = MonitorClient("http://127.0.0.1:8765")
    img = m.screenshot_image()               # PIL 이미지
    m.click(120, 80)                          # 클릭
    m.type("안녕하세요"); m.key("enter")       # 타이핑 + 엔터
    m.wait_for_color(300, 200, "#22c55e")     # 그 점이 초록색이 될 때까지 대기
"""

from __future__ import annotations

import base64
import io
import time
from typing import Any, Sequence

import httpx


class MonitorError(RuntimeError):
    pass


def _hex_to_rgb(color: str | Sequence[int]) -> tuple[int, int, int]:
    if isinstance(color, str):
        c = color.lstrip("#")
        if len(c) != 6:
            raise ValueError(f"색은 #rrggbb 형식이어야 합니다: {color!r}")
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    r, g, b = color[:3]
    return int(r), int(g), int(b)


class MonitorClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8765", token: str | None = None, timeout: float = 60.0) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "MonitorClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------ 조회
    def _get(self, path: str, **params: Any) -> httpx.Response:
        r = self._http.get(path, params={k: v for k, v in params.items() if v is not None})
        if r.status_code >= 400:
            raise MonitorError(f"{r.status_code}: {r.text}")
        return r

    def info(self) -> dict[str, Any]:
        return self._get("/api/info").json()

    def size(self) -> tuple[int, int]:
        w, h = self.info()["size"]
        return int(w), int(h)

    def screenshot(self, fmt: str = "png", max_width: int | None = None, max_height: int | None = None,
                   grid: int | None = None, cursor: bool = False, fresh: bool = True,
                   quality: int = 85) -> tuple[bytes, dict[str, Any]]:
        """(이미지 바이트, 메타) 를 반환. 메타의 scale = 이미지 픽셀 / 원본 픽셀."""
        r = self._get("/api/screenshot", format=fmt, max_width=max_width, max_height=max_height, grid=grid,
                      cursor=str(cursor).lower(), fresh=str(fresh).lower(), quality=quality)
        h = r.headers
        meta = {"mime": h.get("content-type"), "width": int(h["x-image-width"]), "height": int(h["x-image-height"]),
                "frame_width": int(h["x-frame-width"]), "frame_height": int(h["x-frame-height"]),
                "scale": float(h["x-scale"]), "seq": int(h["x-frame-seq"])}
        return r.content, meta

    def screenshot_image(self, **kw: Any) -> Any:
        from PIL import Image

        data, _ = self.screenshot(**kw)
        return Image.open(io.BytesIO(data)).convert("RGB")

    def pixel(self, x: int, y: int, fresh: bool = False) -> tuple[int, int, int]:
        r = self._get("/api/pixel", x=x, y=y, fresh=str(fresh).lower()).json()
        return tuple(r["rgb"])  # type: ignore[return-value]

    def wait_for_color(self, x: int, y: int, color: str | Sequence[int], tolerance: int = 16,
                       timeout: float = 30.0, interval: float = 0.2) -> bool:
        """(x,y) 의 색이 color 와 비슷해질 때까지 기다립니다. 시간 안에 되면 True."""
        target = _hex_to_rgb(color)
        deadline = time.time() + timeout
        while time.time() < deadline:
            cur = self.pixel(x, y, fresh=True)
            if all(abs(a - b) <= tolerance for a, b in zip(cur, target)):
                return True
            time.sleep(interval)
        return False

    # ------------------------------------------------------------------ 입력
    def actions(self, actions: list[dict[str, Any]] | dict[str, Any], space: Any = None,
                screenshot: bool | dict[str, Any] = False, settle_ms: int = 300, check: bool = True) -> dict[str, Any]:
        """액션 묶음 실행. check=True 면 하나라도 실패 시 MonitorError."""
        body: dict[str, Any] = {"actions": actions, "screenshot": screenshot, "settle_ms": settle_ms}
        if space is not None:
            body["space"] = space
        r = self._http.post("/api/actions", json=body)
        if r.status_code >= 400:
            raise MonitorError(f"{r.status_code}: {r.text}")
        out = r.json()
        if check and not out.get("ok"):
            bad = [x for x in out["results"] if not x.get("ok")]
            raise MonitorError(f"액션 실패: {bad[0].get('error') if bad else out}")
        return out

    def act(self, action: str, **fields: Any) -> dict[str, Any]:
        return self.actions([{"action": action, **fields}])

    def move(self, x: float, y: float) -> None:
        self.act("move", x=x, y=y)

    def click(self, x: float | None = None, y: float | None = None, button: str = "left", count: int = 1,
              modifiers: str | None = None) -> None:
        self.act("click", x=x, y=y, button=button, count=count, modifiers=modifiers)

    def double_click(self, x: float | None = None, y: float | None = None) -> None:
        self.act("double_click", x=x, y=y)

    def right_click(self, x: float | None = None, y: float | None = None) -> None:
        self.act("right_click", x=x, y=y)

    def drag(self, x1: float, y1: float, x2: float, y2: float, button: str = "left", steps: int = 10) -> None:
        self.act("drag", **{"from": [x1, y1], "to": [x2, y2], "button": button, "steps": steps})

    def scroll(self, dy: int = 0, dx: int = 0, x: float | None = None, y: float | None = None) -> None:
        self.act("scroll", dx=dx, dy=dy, x=x, y=y)

    def key(self, keys: str, repeat: int = 1) -> None:
        self.act("key", keys=keys, repeat=repeat)

    def type(self, text: str, interval_ms: float = 0) -> None:
        self.act("type", text=text, interval_ms=interval_ms)

    def wait(self, ms: float) -> None:
        time.sleep(ms / 1000)

    def navigate(self, url: str) -> None:
        self.act("navigate", url=url)


def decode_screenshot(shot: dict[str, Any]) -> bytes:
    """/api/actions 응답의 screenshot(base64) → 이미지 바이트."""
    return base64.b64decode(shot["data"])
