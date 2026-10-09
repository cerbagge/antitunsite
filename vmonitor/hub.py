"""프레임 허브 - '모니터 신호'를 한 번만 캡처해서 여러 시청자(MJPEG, WebSocket, AI)에게 나눠 줍니다.

- 시청자가 있으면 설정한 fps 로 계속 캡처하고, 아무도 안 보면 캡처를 멈춰 CPU 를 아낍니다.
- AI 처럼 '지금 화면'이 필요한 요청은 fresh() 로 즉시 새로 캡처합니다.
- 인코딩 결과(JPEG/PNG, 축소, 격자)는 프레임마다 캐시되어 같은 요청이 반복돼도 한 번만 인코딩합니다.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from dataclasses import dataclass, field
from typing import AsyncIterator

from PIL import Image, ImageDraw

from .backends.base import Backend

log = logging.getLogger(__name__)


@dataclass
class Encoded:
    data: bytes
    mime: str
    width: int
    height: int
    #: 인코딩된 이미지 픽셀 / 원본 프레임 픽셀 (1.0 = 원본 크기)
    scale: float


@dataclass
class Frame:
    seq: int
    image: Image.Image
    ts: float
    _cache: dict[tuple, Encoded] = field(default_factory=dict, repr=False)

    @property
    def size(self) -> tuple[int, int]:
        return self.image.size

    def encode(self, fmt: str = "jpeg", quality: int = 80, max_width: int | None = None,
               max_height: int | None = None, grid: int | None = None,
               cursor: tuple[int, int] | None = None) -> Encoded:
        fmt = "jpeg" if fmt.lower() in ("jpg", "jpeg") else "png"
        key = (fmt, quality, max_width, max_height, grid, cursor)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        img = self.image
        w, h = img.size
        scale = fit_scale(w, h, max_width, max_height)
        if scale < 1.0:
            img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
        if grid or cursor:
            img = img.copy()
            if grid:
                draw_grid(img, grid, scale)
            if cursor:
                draw_cursor(img, cursor, scale)
        buf = io.BytesIO()
        if fmt == "jpeg":
            img.save(buf, "JPEG", quality=max(1, min(int(quality), 95)), optimize=False)
            mime = "image/jpeg"
        else:
            img.save(buf, "PNG", compress_level=1)
            mime = "image/png"
        enc = Encoded(buf.getvalue(), mime, img.size[0], img.size[1], scale)
        if len(self._cache) > 16:
            self._cache.clear()
        self._cache[key] = enc
        return enc


def fit_scale(w: int, h: int, max_width: int | None, max_height: int | None) -> float:
    s = 1.0
    if max_width and w > max_width:
        s = min(s, max_width / w)
    if max_height and h > max_height:
        s = min(s, max_height / h)
    return s


def draw_grid(img: Image.Image, step: int, scale: float) -> None:
    """원본 좌표 기준 step 픽셀마다 눈금과 좌표 숫자를 그립니다(AI 가 좌표를 가늠하기 쉽도록)."""
    d = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    step_px = step * scale
    if step_px < 12:
        return
    i = 1
    while i * step_px < w:
        x = round(i * step_px)
        d.line((x, 0, x, h), fill=(255, 0, 255, 70), width=1)
        d.text((x + 2, 2), str(i * step), fill=(255, 0, 255, 230))
        i += 1
    i = 1
    while i * step_px < h:
        y = round(i * step_px)
        d.line((0, y, w, y), fill=(255, 0, 255, 70), width=1)
        d.text((2, y + 2), str(i * step), fill=(255, 0, 255, 230))
        i += 1


def draw_cursor(img: Image.Image, cursor: tuple[int, int], scale: float) -> None:
    d = ImageDraw.Draw(img, "RGBA")
    x, y = cursor[0] * scale, cursor[1] * scale
    d.polygon([(x, y), (x, y + 16), (x + 4, y + 12), (x + 7, y + 18), (x + 9, y + 17), (x + 6, y + 11), (x + 11, y + 11)],
              fill=(255, 255, 255, 230), outline=(0, 0, 0, 255))


class FrameHub:
    def __init__(self, backend: Backend, fps: float = 10.0) -> None:
        self.backend = backend
        self.fps = max(0.2, float(fps))
        self._frame: Frame | None = None
        self._seq = 0
        self._cap_lock = asyncio.Lock()
        self._cond = asyncio.Condition()
        self._watchers = 0
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._running = False
        self.last_error: str | None = None
        self.capture_ms: float = 0.0

    @property
    def watchers(self) -> int:
        return self._watchers

    @property
    def frame(self) -> Frame | None:
        return self._frame

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="vmonitor-frame-loop")

    async def stop(self) -> None:
        self._running = False
        self._wake.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _capture(self) -> Frame:
        async with self._cap_lock:
            t0 = time.perf_counter()
            img = await self.backend.capture()
            if img.mode != "RGB":
                img = img.convert("RGB")
            self.capture_ms = (time.perf_counter() - t0) * 1000
            self._seq += 1
            frame = Frame(self._seq, img, time.time())
            self._frame = frame
            self.last_error = None
        async with self._cond:
            self._cond.notify_all()
        return frame

    async def fresh(self) -> Frame:
        """지금 화면을 새로 캡처합니다(실패하면 예외)."""
        try:
            return await self._capture()
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            raise

    async def latest(self, max_age: float | None = None) -> Frame:
        f = self._frame
        if f is None or (max_age is not None and time.time() - f.ts > max_age):
            return await self.fresh()
        return f

    async def _loop(self) -> None:
        while self._running:
            if self._watchers == 0:
                self._wake.clear()
                await self._wake.wait()
                continue
            t0 = time.perf_counter()
            try:
                await self._capture()
            except Exception as e:  # 창이 잠시 사라지는 등: 마지막 프레임 유지하고 계속
                msg = f"{type(e).__name__}: {e}"
                if msg != self.last_error:
                    log.warning("캡처 실패: %s", msg)
                self.last_error = msg
                await asyncio.sleep(1.0)
                continue
            delay = 1.0 / self.fps - (time.perf_counter() - t0)
            if delay > 0:
                await asyncio.sleep(delay)

    async def frames(self, max_fps: float | None = None) -> AsyncIterator[Frame]:
        """새 프레임이 나올 때마다 내보내는 비동기 반복자. 시청자 수에 포함됩니다."""
        self._watchers += 1
        self._wake.set()
        min_gap = 1.0 / max_fps if max_fps else 0.0
        last_seq = -1
        last_sent = 0.0
        try:
            if self._frame is None:
                try:
                    await self.fresh()
                except Exception:
                    pass
            while True:
                f = self._frame
                if f is not None and f.seq != last_seq:
                    gap = time.perf_counter() - last_sent
                    if gap < min_gap:
                        await asyncio.sleep(min_gap - gap)
                        f = self._frame
                    last_seq = f.seq
                    last_sent = time.perf_counter()
                    yield f
                    continue
                async with self._cond:
                    try:
                        await asyncio.wait_for(self._cond.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        pass
        finally:
            self._watchers -= 1
