"""브라우저 백엔드 (Playwright).

브라우저 한 개를 띄워(기본: 화면 없는 headless) 그 탭을 모니터처럼 송출합니다.
입력은 브라우저 내부(CDP)로 직접 들어가므로 **실제 PC 의 마우스·키보드를 전혀 빼앗지 않고**,
창이 가려지거나 최소화되어도 동작합니다. 여러 개를 동시에 띄워도 서로 간섭하지 않습니다.

네트워크 감시(``watch``): 페이지가 스스로 받아 오는 JSON(XHR/fetch) 응답을 옆에서 기록합니다.
안티봇 때문에 JSON 주소를 직접 요청할 수 없는 사이트도, 실제 브라우저가 챌린지를 통과한 뒤 받은
데이터는 그대로 읽을 수 있습니다(서버에 요청을 더 보내지 않음). ``players()`` 는 그중 플레이어 목록
(Dynmap/LiveAtlas, squaremap, BlueMap 형식)을 찾아 공통 형식으로 돌려줍니다.

필요 패키지: ``pip install playwright`` 후 ``playwright install chromium``
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import time
from collections import OrderedDict
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

#: 자동화 표시를 숨기는 스크립트 (stealth). 페이지 스크립트보다 먼저 실행됩니다.
STEALTH_INIT = "Object.defineProperty(Navigator.prototype, 'webdriver', {get: () => undefined, configurable: true});"

MAX_CAPTURES = 50               # 기록해 둘 응답 수(주소별 최신 1개, 오래된 것부터 버림)
MAX_CAPTURE_BYTES = 5_000_000   # 이보다 큰 응답은 기록하지 않음


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
                 stealth: bool = False, user_agent: str | None = None, extra_headers: dict[str, str] | None = None,
                 watch: list[str] | None = None, **kw: Any) -> None:
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
        self.stealth = stealth
        self.user_agent = user_agent
        self.extra_headers = dict(extra_headers or {})
        # 감시할 응답 주소 정규식. 비어 있으면 모든 XHR/fetch JSON 을 기록합니다.
        self.watch = [re.compile(w) for w in (watch or [])]
        self._captures: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._capture_seq = 0
        self._record_tasks: set[asyncio.Future[None]] = set()
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
        args = list(self.args)
        if self.stealth and self.browser_name == "chromium":
            # 'Chrome 이 자동화 소프트웨어에 의해 제어되고 있습니다' 표시와 navigator.webdriver 를 없앱니다.
            launch["ignore_default_args"] = ["--enable-automation"]
            args.append("--disable-blink-features=AutomationControlled")
        if args:
            launch["args"] = args
        ctx_opts: dict[str, Any] = {"viewport": self.viewport, "device_scale_factor": 1}
        if self.user_agent:
            ctx_opts["user_agent"] = self.user_agent
        if self.extra_headers:
            ctx_opts["extra_http_headers"] = self.extra_headers
        if self.user_data_dir:
            # 로그인 상태·쿠키를 유지하는 영구 프로필 (안티봇 챌린지 통과 쿠키도 유지됨)
            self._context = await btype.launch_persistent_context(self.user_data_dir, **ctx_opts, **launch)
        else:
            self._browser = await btype.launch(**launch)
            self._context = await self._browser.new_context(**ctx_opts)
        if self.stealth:
            await self._context.add_init_script(STEALTH_INIT)
        self._context.on("page", self._on_new_page)
        self._context.on("response", self._on_response)
        pages = self._context.pages
        self._page = pages[0] if pages else await self._context.new_page()
        if self.url:
            await self.navigate(self.url)

    async def stop(self) -> None:
        for t in list(self._record_tasks):
            t.cancel()
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

    # ------------------------------------------------------------------ 네트워크 감시
    def _on_response(self, resp: Any) -> None:
        try:
            if resp.request.resource_type not in ("xhr", "fetch"):
                return
            url = resp.url
        except Exception:
            return
        if self.watch and not any(w.search(url) for w in self.watch):
            return
        t = asyncio.ensure_future(self._record(resp, url))
        self._record_tasks.add(t)  # 작업이 끝나기 전에 GC 되지 않도록 참조 유지
        t.add_done_callback(self._record_tasks.discard)

    async def _record(self, resp: Any, url: str) -> None:
        try:
            body = await resp.body()
            if len(body) > MAX_CAPTURE_BYTES:
                return
            data = json.loads(body)
        except Exception:  # JSON 이 아니거나 본문을 읽을 수 없는 응답(리다이렉트 등)
            return
        self._capture_seq += 1
        self._captures.pop(url, None)
        self._captures[url] = {"url": url, "status": resp.status, "ts": time.time(),
                               "seq": self._capture_seq, "data": data}
        while len(self._captures) > MAX_CAPTURES:
            self._captures.popitem(last=False)

    def captures(self, pattern: str | None = None) -> list[dict[str, Any]]:
        rx = re.compile(pattern) if pattern else None
        items = [c for c in self._captures.values() if rx is None or rx.search(c["url"])]
        return sorted(items, key=lambda c: c["seq"], reverse=True)

    def players(self) -> dict[str, Any]:
        for c in self.captures():
            found = find_players(c["data"])
            if found is not None:
                return {"source": c["url"], "ts": c["ts"], "age_s": round(time.time() - c["ts"], 1),
                        "count": len(found), "players": found}
        return {"source": None, "ts": None, "age_s": None, "count": 0, "players": [],
                "note": "아직 플레이어 목록이 담긴 응답을 받지 못했습니다(페이지 로드·챌린지 통과 후 몇 초 기다려 주세요)"}

    async def evaluate(self, expression: str, timeout: float = 30.0) -> Any:
        try:
            return await asyncio.wait_for(self.page.evaluate(expression), timeout)
        except asyncio.TimeoutError as e:
            raise BackendError(f"스크립트 실행이 {timeout:.0f}초 안에 끝나지 않았습니다") from e
        except BackendError:
            raise
        except Exception as e:
            raise BackendError(f"스크립트 실행 실패: {e}") from e

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
            return {"url": p.url, "tabs": tabs, "headless": self.headless, "stealth": self.stealth,
                    "captures": len(self._captures)}
        except Exception as e:
            return {"error": str(e)}


def _num_or_none(v: Any) -> float | None:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _player(p: Any) -> dict[str, Any] | None:
    if not isinstance(p, dict):
        return None
    pos = p.get("position") if isinstance(p.get("position"), dict) else p  # BlueMap 은 position 안에 좌표
    x, y, z = (_num_or_none(pos.get(k)) for k in ("x", "y", "z"))
    name = p.get("name") or p.get("account") or p.get("displayName")
    if name is None and x is None:
        return None
    return {"name": name, "account": p.get("account") or p.get("uuid"), "world": p.get("world"),
            "x": x, "y": y, "z": z, "health": p.get("health"), "armor": p.get("armor"),
            "foreign": p.get("foreign")}


def find_players(data: Any, depth: int = 2) -> list[dict[str, Any]] | None:
    """JSON 에서 ``players`` 배열을 찾아 공통 형식(name, account, world, x, y, z, ...)으로 바꿉니다.

    Dynmap/LiveAtlas(``up/world/...``), squaremap(``players.json``), BlueMap(``live/players.json``) 형식을 지원합니다.
    플레이어가 0명인 목록도 '찾음'으로 보고 빈 리스트를 돌려주며, 목록 자체가 없으면 None 입니다.
    """
    if not isinstance(data, dict):
        return None
    ps = data.get("players")
    if isinstance(ps, list):
        return [x for x in (_player(p) for p in ps) if x is not None]
    if depth > 0:
        for v in data.values():
            found = find_players(v, depth - 1)
            if found is not None:
                return found
    return None
