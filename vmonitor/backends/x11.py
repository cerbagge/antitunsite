"""X11 백엔드 (Linux).

두 가지로 씁니다.
1. **가상 모니터(Xvfb)**: ``--xvfb`` 로 화면 없는 가상 디스플레이를 새로 만들고 ``--launch`` 로 앱을 그 안에서
   실행합니다. 실제 모니터·마우스와 완전히 분리된 '전용 모니터'가 생기는 셈입니다.
2. **기존 디스플레이**: ``--display :0`` 과 ``--window "제목 정규식"`` 으로 이미 떠 있는 창 영역만 송출합니다.
   (이 경우 XTest 입력은 그 디스플레이의 실제 포인터를 움직입니다.)

입력은 XTest 확장(가짜 하드웨어 입력)으로 넣으므로 대부분의 X 앱이 실제 입력과 똑같이 받아들입니다.
필요 패키지: ``pip install python-xlib``, 가상 모니터를 쓰려면 ``Xvfb``(apt install xvfb)
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import subprocess
import threading
import time
from typing import Any

from PIL import Image

from .base import Backend, BackendError, NotSupportedError

log = logging.getLogger(__name__)

X11_KEYSYMS = {
    "enter": "Return", "esc": "Escape", "tab": "Tab", "backspace": "BackSpace", "delete": "Delete",
    "insert": "Insert", "home": "Home", "end": "End", "pageup": "Prior", "pagedown": "Next",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right", "space": "space",
    "capslock": "Caps_Lock", "numlock": "Num_Lock", "scrolllock": "Scroll_Lock", "printscreen": "Print",
    "pause": "Pause", "contextmenu": "Menu",
    "shift": "Shift_L", "ctrl": "Control_L", "alt": "Alt_L", "meta": "Super_L",
    "hangul": "Hangul", "hanja": "Hangul_Hanja",
    **{f"f{i}": f"F{i}" for i in range(1, 25)},
}

BUTTON_CODES = {"left": 1, "middle": 2, "right": 3}


def char_keysym(ch: str) -> int:
    """문자 하나 → X11 keysym (Latin-1 은 코드포인트 그대로, 그 밖은 0x01000000 + 코드포인트)."""
    if ch == "\n":
        return 0xFF0D  # Return
    if ch == "\t":
        return 0xFF09  # Tab
    cp = ord(ch)
    if 0x20 <= cp <= 0x7E or 0xA0 <= cp <= 0xFF:
        return cp
    return 0x01000000 | cp


class X11Backend(Backend):
    name = "x11"

    def __init__(self, display: str | None = None, window: str | None = None, window_id: str | int | None = None,
                 xvfb: bool = False, width: int = 1280, height: int = 800, launch: str | None = None,
                 window_timeout: float = 20.0, **kw: Any) -> None:
        super().__init__(**kw)
        self.display_name = display
        self.window_pattern = window
        self.window_id = int(window_id, 0) if isinstance(window_id, str) else window_id
        self.use_xvfb = xvfb
        self.screen_size = (int(width), int(height))
        self.launch_cmd = launch
        self.window_timeout = window_timeout
        self._xvfb: subprocess.Popen | None = None
        self._app: subprocess.Popen | None = None
        self._d = None  # 입력용 연결
        self._dc = None  # 캡처용 연결
        self._lock = threading.Lock()
        self._clock = threading.Lock()
        self._win = None
        self._origin = (0, 0)
        self._spare: list[int] = []
        self._spare_idx = 0
        self._spare_orig: dict[int, list[int]] = {}

    # ------------------------------------------------------------------ 수명 주기
    async def start(self) -> None:
        try:
            from Xlib import display as xdisplay  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise BackendError("python-xlib 가 없습니다: pip install python-xlib") from e
        await asyncio.to_thread(self._start_sync)
        if self.window_pattern or self.window_id:
            deadline = time.time() + self.window_timeout
            while True:
                try:
                    await asyncio.to_thread(self._find_window)
                    break
                except BackendError:
                    if time.time() > deadline:
                        raise
                    await asyncio.sleep(0.5)
        await asyncio.to_thread(self._region)

    def _start_sync(self) -> None:
        from Xlib import display as xdisplay
        from Xlib.ext import xtest  # noqa: F401  (확장 존재 확인)

        if self.use_xvfb:
            self._spawn_xvfb()
        name = self.display_name or os.environ.get("DISPLAY")
        if not name:
            raise BackendError("X 디스플레이가 없습니다. --display :0 을 주거나 --xvfb 로 가상 모니터를 만드세요")
        self.display_name = name
        self._d = xdisplay.Display(name)
        self._dc = xdisplay.Display(name)
        if not self._d.has_extension("XTEST"):
            raise BackendError("이 X 서버에는 XTEST 확장이 없어 입력을 보낼 수 없습니다")
        if self.launch_cmd:
            env = {**os.environ, "DISPLAY": name}
            log.info("앱 실행: %s (DISPLAY=%s)", self.launch_cmd, name)
            self._app = subprocess.Popen(self.launch_cmd, shell=True, env=env, start_new_session=True,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _spawn_xvfb(self) -> None:
        if self.display_name:
            num = int(self.display_name.lstrip(":").split(".")[0])
        else:
            num = next(n for n in range(99, 200)
                       if not os.path.exists(f"/tmp/.X11-unix/X{n}") and not os.path.exists(f"/tmp/.X{n}-lock"))
        w, h = self.screen_size
        log.info("가상 모니터 시작: Xvfb :%d %dx%d", num, w, h)
        try:
            self._xvfb = subprocess.Popen(["Xvfb", f":{num}", "-screen", "0", f"{w}x{h}x24", "-nolisten", "tcp"],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError as e:
            raise BackendError("Xvfb 가 설치되어 있지 않습니다 (apt install xvfb)") from e
        sock = f"/tmp/.X11-unix/X{num}"
        for _ in range(100):
            if os.path.exists(sock):
                break
            if self._xvfb.poll() is not None:
                raise BackendError(f"Xvfb :{num} 시작 실패")
            time.sleep(0.05)
        self.display_name = f":{num}"

    async def stop(self) -> None:
        await asyncio.to_thread(self._stop_sync)

    def _stop_sync(self) -> None:
        if self._d is not None and self._spare_orig:
            try:
                for code, syms in self._spare_orig.items():
                    self._d.change_keyboard_mapping(code, [syms])
                self._d.sync()
            except Exception:
                pass
        for conn in (self._d, self._dc):
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        self._d = self._dc = None
        if self._app is not None and self._app.poll() is None:
            try:
                os.killpg(self._app.pid, signal.SIGTERM)
            except Exception:
                self._app.terminate()
        if self._xvfb is not None and self._xvfb.poll() is None:
            self._xvfb.terminate()
            try:
                self._xvfb.wait(3)
            except subprocess.TimeoutExpired:
                self._xvfb.kill()

    # ------------------------------------------------------------------ 창 찾기 / 영역
    def _window_title(self, d: Any, win: Any) -> str:
        try:
            net = win.get_full_property(d.intern_atom("_NET_WM_NAME"), d.intern_atom("UTF8_STRING"))
            if net and net.value:
                v = net.value
                return v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)
            name = win.get_wm_name()
            if isinstance(name, bytes):
                return name.decode("latin-1", "replace")
            return name or ""
        except Exception:
            return ""

    def list_windows(self) -> list[dict[str, Any]]:
        """보이는 창 목록(id, 제목, 위치·크기)."""
        from Xlib import X

        d = self._d
        root = d.screen().root
        out: list[dict[str, Any]] = []

        def walk(w: Any, depth: int) -> None:
            try:
                children = w.query_tree().children
            except Exception:
                return
            for c in children:
                try:
                    attrs = c.get_attributes()
                    title = self._window_title(d, c)
                    if attrs.map_state == X.IsViewable and title:
                        g = c.get_geometry()
                        pos = root.translate_coords(c, 0, 0)
                        out.append({"id": hex(c.id), "title": title, "x": pos.x, "y": pos.y,
                                    "width": g.width, "height": g.height})
                except Exception:
                    continue
                if depth < 4:
                    walk(c, depth + 1)

        with self._lock:
            walk(root, 0)
        return out

    def _find_window(self) -> None:
        d = self._d
        if self.window_id:
            self._win = d.create_resource_object("window", self.window_id)
            return
        pat = re.compile(self.window_pattern, re.IGNORECASE)
        best = None
        for w in self.list_windows():
            # vmonitor 를 실행한 터미널(제목에 명령줄)이나 뷰어 탭이 대상으로 잡히지 않게 제외
            if "vmonitor" in w["title"].lower():
                continue
            if pat.search(w["title"]):
                area = w["width"] * w["height"]
                if best is None or area > best[0]:
                    best = (area, w)
        if best is None:
            raise BackendError(f"제목이 /{self.window_pattern}/ 와 맞는 창을 찾지 못했습니다")
        log.info("창 선택: %s %r", best[1]["id"], best[1]["title"])
        self._win = d.create_resource_object("window", int(best[1]["id"], 16))

    def _region(self) -> tuple[int, int, int, int]:
        d = self._dc
        root = d.screen().root
        sw, sh = d.screen().width_in_pixels, d.screen().height_in_pixels
        if self._win is None:
            x, y, w, h = 0, 0, sw, sh
        else:
            with self._clock:
                win = d.create_resource_object("window", self._win.id)
                try:
                    g = win.get_geometry()
                    pos = root.translate_coords(win, 0, 0)
                except Exception as e:
                    raise BackendError(f"창을 더 이상 찾을 수 없습니다: {e}") from e
            x, y, w, h = pos.x, pos.y, g.width, g.height
            # 화면 밖으로 나간 부분은 잘라냄
            x0, y0 = max(0, x), max(0, y)
            x1, y1 = min(sw, x + w), min(sh, y + h)
            if x1 <= x0 or y1 <= y0:
                raise BackendError("창이 화면 밖에 있습니다")
            x, y, w, h = x0, y0, x1 - x0, y1 - y0
        self._origin = (x, y)
        self._size = (w, h)
        return x, y, w, h

    # ------------------------------------------------------------------ 캡처
    async def capture(self) -> Image.Image:
        return await asyncio.to_thread(self._capture_sync)

    def _capture_sync(self) -> Image.Image:
        from Xlib import X

        x, y, w, h = self._region()
        with self._clock:
            raw = self._dc.screen().root.get_image(x, y, w, h, X.ZPixmap, 0xFFFFFFFF)
        data = raw.data if isinstance(raw.data, bytes) else bytes(raw.data)
        if len(data) < w * h * 4:
            raise BackendError(f"지원하지 않는 화면 형식입니다 (depth={raw.depth})")
        return Image.frombuffer("RGB", (w, h), data, "raw", "BGRX", 0, 1)

    # ------------------------------------------------------------------ 입력
    async def _x(self, fn: Any, *args: Any) -> Any:
        def run() -> Any:
            with self._lock:
                r = fn(*args)
                self._d.sync()
                return r
        return await asyncio.to_thread(run)

    def _fake(self, event: int, detail: int = 0, x: int | None = None, y: int | None = None) -> None:
        from Xlib.ext import xtest

        if x is None:
            xtest.fake_input(self._d, event, detail)
        else:
            xtest.fake_input(self._d, event, x=x, y=y)

    async def _move(self, x: int, y: int) -> None:
        from Xlib import X

        ox, oy = self._origin
        await self._x(self._fake, X.MotionNotify, 0, ox + x, oy + y)

    async def _button(self, button: str, down: bool, click_count: int = 1) -> None:
        from Xlib import X

        await self._x(self._fake, X.ButtonPress if down else X.ButtonRelease, BUTTON_CODES[button])

    async def _wheel(self, dx: int, dy: int) -> None:
        from Xlib import X

        def run() -> None:
            for code, n in ((5 if dy > 0 else 4, abs(dy)), (7 if dx > 0 else 6, abs(dx))):
                for _ in range(n):
                    self._fake(X.ButtonPress, code)
                    self._fake(X.ButtonRelease, code)
        await self._x(run)

    def _keycode(self, keysym: int) -> tuple[int, bool]:
        """keysym → (keycode, shift 필요 여부). 매핑이 없으면 빈 키코드에 임시로 매핑합니다."""
        best = None
        for code, index in self._d.keysym_to_keycodes(keysym):
            if index in (0, 1) and (best is None or index < best[1]):
                best = (code, index)
        if best is not None:
            return best[0], best[1] == 1
        return self._remap(keysym), False

    def _remap(self, keysym: int) -> int:
        d = self._d
        if not self._spare:
            first = d.display.info.min_keycode
            count = d.display.info.max_keycode - first + 1
            mapping = d.get_keyboard_mapping(first, count)
            self._spare = [first + i for i, syms in enumerate(mapping) if not any(syms)][-8:]
            if not self._spare:
                raise NotSupportedError("빈 키코드가 없어 이 문자를 입력할 수 없습니다")
        code = self._spare[self._spare_idx % len(self._spare)]
        self._spare_idx += 1
        if code not in self._spare_orig:
            self._spare_orig[code] = [0, 0]
        d.change_keyboard_mapping(code, [(keysym, keysym)])
        d.sync()
        time.sleep(0.02)  # 앱이 MappingNotify 를 처리할 시간
        return code

    def _keysym_for(self, key: str) -> int:
        from Xlib import XK

        name = X11_KEYSYMS.get(key)
        if name:
            ks = XK.string_to_keysym(name)
            if ks:
                return ks
            raise NotSupportedError(f"X 서버가 모르는 키입니다: {key}")
        if len(key) == 1:
            return char_keysym(key)
        raise NotSupportedError(f"지원하지 않는 키: {key!r}")

    async def _key(self, key: str, down: bool) -> None:
        from Xlib import X

        def run() -> None:
            code, _shift = self._keycode(self._keysym_for(key))
            self._fake(X.KeyPress if down else X.KeyRelease, code)
        await self._x(run)

    async def _type(self, text: str) -> None:
        from Xlib import X

        shift_code = None

        def run() -> None:
            nonlocal shift_code
            for ch in text:
                code, need_shift = self._keycode(char_keysym(ch))
                if need_shift:
                    if shift_code is None:
                        shift_code = self._keycode(self._keysym_for("shift"))[0]
                    self._fake(X.KeyPress, shift_code)
                self._fake(X.KeyPress, code)
                self._fake(X.KeyRelease, code)
                if need_shift:
                    self._fake(X.KeyRelease, shift_code)
                self._d.sync()
                time.sleep(0.005)
        await self._x(run)

    def info(self) -> dict[str, Any]:
        out: dict[str, Any] = {"display": self.display_name, "origin": list(self._origin),
                               "xvfb": self._xvfb is not None}
        if self._win is not None:
            out["window_id"] = hex(self._win.id)
            try:
                with self._clock:
                    out["window_title"] = self._window_title(self._dc, self._dc.create_resource_object("window", self._win.id))
            except Exception:
                pass
        if self._app is not None:
            out["app_running"] = self._app.poll() is None
        return out
