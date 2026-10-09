"""백엔드 공통 인터페이스.

백엔드 = '모니터에 비춰 줄 대상' 하나(브라우저 탭, 특정 앱 창, 가상 디스플레이 등)를 캡처하고
그 대상에 입력을 넣는 구현체입니다. 좌표는 항상 **캡처된 프레임의 픽셀 좌표**(왼쪽 위 0,0)입니다.

각 백엔드는 아래 '원시 동작'만 구현하면 되고, 클릭·드래그·단축키 같은 복합 동작은
이 기반 클래스가 원시 동작을 조합해 처리합니다(필요하면 백엔드가 덮어쓸 수 있습니다).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Iterable, Sequence

from PIL import Image

from ..keys import MODIFIERS, SHIFTED_SYMBOLS, Combo

log = logging.getLogger(__name__)

BUTTONS = ("left", "right", "middle")


class BackendError(RuntimeError):
    """백엔드 동작 실패(창이 사라짐, 입력 거부 등)."""


class NotSupportedError(BackendError):
    """이 백엔드가 지원하지 않는 동작."""


class Backend(ABC):
    #: 백엔드 식별자 (info 에 표시)
    name: str = "base"

    def __init__(self, input_delay: float = 0.01) -> None:
        self.input_delay = max(0.0, input_delay)
        self._cursor: tuple[int, int] = (0, 0)
        self._size: tuple[int, int] = (0, 0)
        self._buttons_down: set[str] = set()
        self._keys_down: list[str] = []

    # ------------------------------------------------------------------ 수명 주기
    async def start(self) -> None:  # pragma: no cover - 기본은 아무 것도 안 함
        pass

    async def stop(self) -> None:  # pragma: no cover
        pass

    # ------------------------------------------------------------------ 원시 동작 (백엔드 구현)
    @abstractmethod
    async def capture(self) -> Image.Image:
        """현재 화면을 RGB 이미지로 반환합니다. 구현체는 self._size 를 갱신해야 합니다."""

    @abstractmethod
    async def _move(self, x: int, y: int) -> None: ...

    @abstractmethod
    async def _button(self, button: str, down: bool, click_count: int = 1) -> None:
        """현재 커서 위치에서 버튼을 누르거나 뗍니다. click_count 는 연속 클릭에서 몇 번째인지(1,2,3)."""

    @abstractmethod
    async def _wheel(self, dx: int, dy: int) -> None:
        """현재 커서 위치에서 휠을 굴립니다. 단위는 '노치(칸)', dy>0 이면 아래로, dx>0 이면 오른쪽."""

    @abstractmethod
    async def _key(self, key: str, down: bool) -> None:
        """정규 키 이름 하나를 누르거나 뗍니다."""

    @abstractmethod
    async def _type(self, text: str) -> None:
        """문자열을 그대로 입력합니다(한글 등 유니코드 포함)."""

    async def navigate(self, url: str) -> None:
        raise NotSupportedError(f"{self.name} 백엔드는 navigate 를 지원하지 않습니다")

    def info(self) -> dict[str, Any]:
        """백엔드별 부가 정보(창 제목, URL 등)."""
        return {}

    # ------------------------------------------------------------------ 공개 API
    @property
    def size(self) -> tuple[int, int]:
        return self._size

    @property
    def cursor(self) -> tuple[int, int]:
        return self._cursor

    async def _pause(self, factor: float = 1.0) -> None:
        if self.input_delay > 0:
            await asyncio.sleep(self.input_delay * factor)

    def _clamp(self, x: float, y: float) -> tuple[int, int]:
        w, h = self._size
        xi, yi = int(round(x)), int(round(y))
        if w > 0 and h > 0:
            xi = min(max(xi, 0), w - 1)
            yi = min(max(yi, 0), h - 1)
        return xi, yi

    async def move(self, x: float, y: float) -> None:
        xi, yi = self._clamp(x, y)
        await self._move(xi, yi)
        self._cursor = (xi, yi)

    async def mouse_down(self, button: str = "left", count: int = 1) -> None:
        """count: 연속 클릭 중 몇 번째 누름인지(사람이 뷰어에서 더블클릭할 때 2)."""
        _check_button(button)
        await self._button(button, True, max(1, int(count)))
        self._buttons_down.add(button)

    async def mouse_up(self, button: str = "left", count: int = 1) -> None:
        _check_button(button)
        await self._button(button, False, max(1, int(count)))
        self._buttons_down.discard(button)

    async def click(self, x: float | None = None, y: float | None = None, button: str = "left",
                    count: int = 1, modifiers: Sequence[str] = ()) -> None:
        _check_button(button)
        if x is not None and y is not None:
            await self.move(x, y)
            await self._pause()
        async with self.holding(modifiers):
            for n in range(1, max(1, int(count)) + 1):
                await self._button(button, True, n)
                await self._pause()
                await self._button(button, False, n)
                if n < count:
                    await self._pause(3)

    async def drag(self, points: Sequence[tuple[float, float]], button: str = "left",
                   steps: int = 10, modifiers: Sequence[str] = ()) -> None:
        """points[0] 에서 누르고, 경로를 따라 이동한 뒤 마지막 점에서 뗍니다."""
        if len(points) < 2:
            raise BackendError("드래그에는 최소 두 점이 필요합니다")
        _check_button(button)
        async with self.holding(modifiers):
            await self.move(*points[0])
            await self._pause()
            await self.mouse_down(button)
            try:
                await self._pause(2)
                prev = points[0]
                for pt in points[1:]:
                    n = max(1, int(steps))
                    for i in range(1, n + 1):
                        t = i / n
                        await self.move(prev[0] + (pt[0] - prev[0]) * t, prev[1] + (pt[1] - prev[1]) * t)
                        await self._pause()
                    prev = pt
                await self._pause(2)
            finally:
                await self.mouse_up(button)

    async def scroll(self, dx: int = 0, dy: int = 0, x: float | None = None, y: float | None = None,
                     modifiers: Sequence[str] = ()) -> None:
        if x is not None and y is not None:
            await self.move(x, y)
            await self._pause()
        async with self.holding(modifiers):
            await self._wheel(int(dx), int(dy))

    async def key_down(self, key: str) -> None:
        await self._key(key, True)
        if key not in self._keys_down:
            self._keys_down.append(key)

    async def key_up(self, key: str) -> None:
        await self._key(key, False)
        if key in self._keys_down:
            self._keys_down.remove(key)

    async def press(self, combo: Combo, repeat: int = 1) -> None:
        """단축키 한 번(또는 repeat 번) 누르기. 수정자를 먼저 누르고 마지막에 뗍니다."""
        key, mods = self._shift_for_symbol(combo)
        async with self.holding(mods):
            for i in range(max(1, int(repeat))):
                await self._key(key, True)
                await self._pause()
                await self._key(key, False)
                if i + 1 < repeat:
                    await self._pause()

    def _shift_for_symbol(self, combo: Combo) -> tuple[str, tuple[str, ...]]:
        """'!' 처럼 Shift 가 필요한 기호를 (기본 키, shift 포함 수정자) 로 바꿉니다.

        키 코드 기반 백엔드(Windows, X11)용입니다. 문자 기반(브라우저)은 덮어써서 그대로 둡니다.
        """
        if combo.key in SHIFTED_SYMBOLS:
            mods = tuple(m for m in MODIFIERS if m in (*combo.modifiers, "shift"))
            return SHIFTED_SYMBOLS[combo.key], mods
        return combo.key, combo.modifiers

    async def type_text(self, text: str, interval: float = 0.0) -> None:
        if not text:
            return
        if interval <= 0:
            await self._type(text)
            return
        for ch in text:
            await self._type(ch)
            await asyncio.sleep(interval)

    async def hold_keys(self, combo: Combo, duration: float) -> None:
        async with self.holding((*combo.modifiers, combo.key)):
            await asyncio.sleep(max(0.0, duration))

    @contextlib.asynccontextmanager
    async def holding(self, keys: Iterable[str]) -> AsyncIterator[None]:
        """keys 를 차례로 누른 상태를 유지하고, 블록을 빠져나올 때 역순으로 뗍니다."""
        pressed: list[str] = []
        try:
            for k in keys:
                await self.key_down(k)
                pressed.append(k)
                await self._pause()
            yield
        finally:
            for k in reversed(pressed):
                with contextlib.suppress(Exception):
                    await self.key_up(k)

    async def release_all(self) -> None:
        """눌린 채 남은 키·버튼을 모두 뗍니다(오류 복구·종료 시)."""
        for k in list(reversed(self._keys_down)):
            with contextlib.suppress(Exception):
                await self.key_up(k)
        for b in list(self._buttons_down):
            with contextlib.suppress(Exception):
                await self.mouse_up(b)


def _check_button(button: str) -> None:
    if button not in BUTTONS:
        raise BackendError(f"알 수 없는 마우스 버튼: {button!r} (left/right/middle)")
