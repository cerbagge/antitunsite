"""브라우저 백엔드 (Playwright).

브라우저 한 개를 띄워(기본: 화면 없는 headless) 그 탭을 모니터처럼 송출합니다.
입력은 브라우저 내부(CDP)로 직접 들어가므로 **실제 PC 의 마우스·키보드를 전혀 빼앗지 않고**,
창이 가려지거나 최소화되어도 동작합니다. 여러 개를 동시에 띄워도 서로 간섭하지 않습니다.

필요 패키지: ``pip install playwright`` 후 ``playwright install chromium``
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Any

from PIL import Image

from .base import Backend, BackendError, NotSupportedError
from ..keys import SHIFTED_SYMBOLS, Combo

log = logging.getLogger(__name__)

PW_KEYS = {
    "enter": "Enter", "esc": "Escape", "tab": "Tab", "backspace": "Backspace", "delete": "Delete",
    "insert": "Insert", "home": "Home", "end": "End", "pageup": "PageUp", "pagedown": "PageDown",
    "up": "ArrowUp", "down": "ArrowDown", "left": "ArrowLeft", "right": "ArrowRight", "space": "Space",
    "capslock": "CapsLock", "numlock": "NumLock", "scrolllock": "ScrollLock", "printscreen": "PrintScreen",
    "pause": "Pause", "contextmenu": "ContextMenu",
    "shift": "Shift", "ctrl": "Control", "alt": "Alt", "meta": "Meta",
    **{f"f{i}": f"F{i}" for i in range(1, 25)},
}


_SHIFT_OF = {base: sym for sym, base in SHIFTED_SYMBOLS.items()}


def pw_key(key: str) -> str:
    if key in PW_KEYS:
        return PW_KEYS[key]
    if len(key) == 1:
        return key
    raise NotSupportedError(f"브라우저 백엔드에서 지원하지 않는 키입니다: {key!r} (한글 등은 type 액션을 쓰세요)")


class BrowserBackend(Backend):
    name = "browser"

    def __init__(self, url: str | None = "about:blank", width: int = 1280, height: int = 800,
                 headless: bool = True, user_data_dir: str | None = None, browser: str = "chromium",
                 executable_path: str | None = None, channel: str | None = None,
                 follow_new_tabs: bool = True, wheel_step: int = 100, args: list[str] | None = None,
                 **kw: Any) -> None:
        super().__init__(**kw)
        self.url = url
        self.viewport = {"width": int(width), "height": int(height)}
        self.headless = headless
        self.user_data_dir = user_data_dir
        self.browser_name = browser
        self.executable_path = executable_path
        self.channel = channel
        self.follow_new_tabs = follow_new_tabs
        self.wheel_step = wheel_step
        self.args = args or []
        self._size = (int(width), int(height))
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._pw_down: dict[str, str] = {}

    # ------------------------------------------------------------------ 수명 주기
    async def start(self) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:  # pragma: no cover
            raise BackendError("Playwright 가 없습니다: pip install playwright && playwright install chromium") from e
        self._pw = await async_playwright().start()
        btype = getattr(self._pw, self.browser_name)
        launch: dict[str, Any] = {"headless": self.headless}
        if self.executable_path:
            launch["executable_path"] = self.executable_path
        if self.channel:
            launch["channel"] = self.channel
        if self.args:
            launch["args"] = self.args
        if self.user_data_dir:
            # 로그인 상태·쿠키를 유지하는 영구 프로필
            self._context = await btype.launch_persistent_context(
                self.user_data_dir, viewport=self.viewport, device_scale_factor=1, **launch)
        else:
            self._browser = await btype.launch(**launch)
            self._context = await self._browser.new_context(viewport=self.viewport, device_scale_factor=1)
        self._context.on("page", self._on_new_page)
        pages = self._context.pages
        self._page = pages[0] if pages else await self._context.new_page()
        if self.url:
            await self.navigate(self.url)

    async def stop(self) -> None:
        for closer in (self._context, self._browser):
            if closer is not None:
                try:
                    await closer.close()
                except Exception:
                    pass
        if self._pw is not None:
            await self._pw.stop()
        self._pw = self._browser = self._context = self._page = None

    def _on_new_page(self, page: Any) -> None:
        if self.follow_new_tabs:
            log.info("새 탭으로 전환: %s", page.url)
            self._page = page

    @property
    def page(self) -> Any:
        p = self._page
        if p is None or p.is_closed():
            open_pages = [x for x in (self._context.pages if self._context else []) if not x.is_closed()]
            if not open_pages:
                raise BackendError("열린 탭이 없습니다")
            p = self._page = open_pages[-1]
        return p

    # ------------------------------------------------------------------ 캡처
    async def capture(self) -> Image.Image:
        last: Exception | None = None
        for _ in range(3):
            try:
                png = await self.page.screenshot(type="png", timeout=10_000)
                img = Image.open(io.BytesIO(png)).convert("RGB")
                self._size = img.size
                return img
            except Exception as e:  # 페이지 전환 중에는 잠깐 실패할 수 있음
                last = e
                await asyncio.sleep(0.2)
        raise BackendError(f"브라우저 캡처 실패: {last}")

    # ------------------------------------------------------------------ 입력
    async def _move(self, x: int, y: int) -> None:
        await self.page.mouse.move(x, y)

    async def _button(self, button: str, down: bool, click_count: int = 1) -> None:
        m = self.page.mouse
        await (m.down(button=button, click_count=click_count) if down else m.up(button=button, click_count=click_count))

    async def _wheel(self, dx: int, dy: int) -> None:
        await self.page.mouse.wheel(dx * self.wheel_step, dy * self.wheel_step)

    async def _key(self, key: str, down: bool) -> None:
        kb = self.page.keyboard
        if down:
            k = pw_key(key)
            # Playwright 는 Shift 를 눌러도 'a' → 'A' 로 바꿔 주지 않으므로 직접 바꿔 보냅니다.
            if len(k) == 1 and "shift" in self._keys_down:
                k = k.upper() if k.isascii() and k.isalpha() else _SHIFT_OF.get(k, k)
            self._pw_down[key] = k
            await kb.down(k)
        else:
            await kb.up(self._pw_down.pop(key, pw_key(key)))

    async def _type(self, text: str) -> None:
        await self.page.keyboard.type(text)

    def _shift_for_symbol(self, combo: Combo) -> tuple[str, tuple[str, ...]]:
        # 브라우저는 '!' 같은 문자를 그대로 입력할 수 있으므로 Shift 변환이 필요 없습니다.
        return combo.key, combo.modifiers

    async def navigate(self, url: str) -> None:
        if "://" not in url and not url.startswith(("about:", "data:", "file:")):
            url = "https://" + url
        await self.page.goto(url, wait_until="domcontentloaded", timeout=30_000)

    def info(self) -> dict[str, Any]:
        try:
            p = self.page
            tabs = len([x for x in self._context.pages if not x.is_closed()])
            return {"url": p.url, "tabs": tabs, "headless": self.headless}
        except Exception as e:
            return {"error": str(e)}
