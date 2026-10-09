"""데모 백엔드 - 외부 프로그램 없이 동작하는 가상 화면.

설치 직후 파이프라인(송출 → AI/매크로가 보고 → 입력)을 바로 확인하거나, 테스트용으로 씁니다.
버튼 3개, 입력 칸, 이벤트 로그가 그려진 화면이며 클릭·타이핑·스크롤·드래그가 그대로 반영됩니다.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .base import Backend

_FONT_CANDIDATES = [
    # Windows
    r"C:\Windows\Fonts\malgun.ttf",
    # macOS
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    # Linux
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


@dataclass
class DemoButton:
    name: str
    box: tuple[int, int, int, int]
    color: tuple[int, int, int]
    clicks: int = 0

    def contains(self, x: int, y: int) -> bool:
        x0, y0, x1, y1 = self.box
        return x0 <= x <= x1 and y0 <= y <= y1


@dataclass
class DemoState:
    text: str = ""
    scroll: tuple[int, int] = (0, 0)
    events: list[str] = field(default_factory=list)
    last_click: tuple[int, int] | None = None
    last_drag: tuple[tuple[int, int], tuple[int, int]] | None = None


class DemoBackend(Backend):
    name = "demo"

    def __init__(self, width: int = 960, height: int = 600, **kw: Any) -> None:
        super().__init__(**kw)
        self._size = (width, height)
        self.state = DemoState()
        w = width
        self.buttons = [
            DemoButton("A", (40, 90, 40 + (w - 160) // 3, 150), (52, 120, 246)),
            DemoButton("B", (80 + (w - 160) // 3, 90, 80 + 2 * (w - 160) // 3, 150), (16, 185, 129)),
            DemoButton("C", (120 + 2 * (w - 160) // 3, 90, w - 40, 150), (245, 158, 11)),
        ]
        self._down_at: dict[str, tuple[int, int]] = {}
        self._mods: set[str] = set()
        self._font = _font(18)
        self._font_big = _font(26)

    def _log(self, msg: str) -> None:
        self.state.events.append(f"{time.strftime('%H:%M:%S')} {msg}")
        del self.state.events[:-10]

    async def capture(self) -> Image.Image:
        w, h = self._size
        img = Image.new("RGB", (w, h), (17, 24, 39))
        d = ImageDraw.Draw(img)
        d.rectangle((0, 0, w, 56), fill=(31, 41, 55))
        d.text((20, 14), "VMonitor Demo Screen", fill=(229, 231, 235), font=self._font_big)
        for b in self.buttons:
            r, g, bl = b.color
            fill = (r, g, bl) if b.clicks % 2 == 0 else (239, 68, 68)
            d.rounded_rectangle(b.box, radius=10, fill=fill)
            cx = (b.box[0] + b.box[2]) // 2
            d.text((cx - 60, b.box[1] + 16), f"Button {b.name}: {b.clicks}", fill=(255, 255, 255), font=self._font)
        # 입력 칸
        d.text((40, 175), "Text input:", fill=(156, 163, 175), font=self._font)
        d.rectangle((40, 200, w - 40, 250), outline=(75, 85, 99), width=2, fill=(3, 7, 18))
        d.text((52, 212), self.state.text[-80:] + "|", fill=(255, 255, 255), font=self._font)
        sx, sy = self.state.scroll
        mods = "+".join(sorted(self._mods)) or "-"
        d.text((40, 270), f"scroll=({sx},{sy})  held={mods}  cursor={self._cursor}", fill=(156, 163, 175), font=self._font)
        # 이벤트 로그
        d.text((40, 310), "Event log:", fill=(156, 163, 175), font=self._font)
        for i, ev in enumerate(self.state.events[-10:]):
            d.text((52, 336 + i * 24), ev, fill=(209, 213, 219), font=self._font)
        if self.state.last_drag:
            (x0, y0), (x1, y1) = self.state.last_drag
            d.line((x0, y0, x1, y1), fill=(168, 85, 247), width=3)
        if self.state.last_click:
            x, y = self.state.last_click
            d.ellipse((x - 7, y - 7, x + 7, y + 7), outline=(239, 68, 68), width=3)
        return img

    async def _move(self, x: int, y: int) -> None:
        pass

    async def _button(self, button: str, down: bool, click_count: int = 1) -> None:
        x, y = self._cursor
        if down:
            self._down_at[button] = (x, y)
            return
        start = self._down_at.pop(button, (x, y))
        if abs(start[0] - x) > 3 or abs(start[1] - y) > 3:
            self.state.last_drag = (start, (x, y))
            self._log(f"drag {button} {start} -> {(x, y)}")
            return
        self.state.last_click = (x, y)
        mods = "+".join(sorted(self._mods))
        self._log(f"click {button} x{click_count} at ({x},{y})" + (f" [{mods}]" if mods else ""))
        if button == "left":
            for b in self.buttons:
                if b.contains(x, y):
                    b.clicks += 1

    async def _wheel(self, dx: int, dy: int) -> None:
        sx, sy = self.state.scroll
        self.state.scroll = (sx + dx, sy + dy)
        self._log(f"scroll dx={dx} dy={dy}")

    async def _key(self, key: str, down: bool) -> None:
        if key in ("ctrl", "alt", "shift", "meta"):
            (self._mods.add if down else self._mods.discard)(key)
            return
        if not down:
            return
        mods = "+".join(sorted(self._mods))
        self._log(f"key {mods + '+' if mods else ''}{key}")
        if key == "backspace":
            self.state.text = self.state.text[:-1]
        elif key == "space" and not self._mods:
            self.state.text += " "
        elif len(key) == 1 and not (self._mods - {"shift"}):
            self.state.text += key.upper() if "shift" in self._mods else key

    async def _type(self, text: str) -> None:
        self.state.text += text
        self._log(f"type {text!r}")

    def info(self) -> dict[str, Any]:
        return {"buttons": {b.name: b.clicks for b in self.buttons}, "text": self.state.text}
