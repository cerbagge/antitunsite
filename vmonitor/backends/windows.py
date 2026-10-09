"""Windows 앱 창 백엔드 (Win32 API, 추가 패키지 불필요).

특정 프로그램 창 하나를 찾아 그 **클라이언트 영역**(제목 표시줄·테두리 제외)만 모니터처럼 송출합니다.

캡처
  ``PrintWindow(PW_RENDERFULLCONTENT)`` 로 창이 다른 창에 가려져 있어도 그 창 내용만 가져옵니다.
  (최소화된 창은 Windows 가 그리지 않으므로 캡처할 수 없습니다 → 최소화하지 말고 뒤에 두세요)

입력 방식 (``input_mode``)
  ``post``      창에 메시지를 직접 보냄(PostMessage). **실제 마우스·포커스를 빼앗지 않고 백그라운드로** 동작.
                메모장·탐색기·대부분의 일반 Win32/WinForms/WPF 앱에서 잘 동작하지만, 게임(DirectInput/
                Raw Input)이나 일부 크롬 기반 앱은 무시할 수 있습니다. Ctrl 조합 단축키도 앱에 따라 안 먹을 수 있습니다.
  ``sendinput`` 창을 앞으로 가져온 뒤 진짜 하드웨어 입력처럼 넣음(SendInput). 거의 모든 앱(게임 포함)에서
                동작하지만 **입력하는 순간 실제 마우스 커서와 포커스가 움직입니다.**

DPI: 고해상도(125%/150%) 화면에서도 좌표가 어긋나지 않도록 프로세스를 Per-Monitor DPI Aware 로 설정합니다.
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import re
import sys
import threading
import time
from ctypes import wintypes
from typing import Any

from PIL import Image

from .base import Backend, BackendError, NotSupportedError

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------- 상수
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN, WM_LBUTTONUP, WM_LBUTTONDBLCLK = 0x0201, 0x0202, 0x0203
WM_RBUTTONDOWN, WM_RBUTTONUP, WM_RBUTTONDBLCLK = 0x0204, 0x0205, 0x0206
WM_MBUTTONDOWN, WM_MBUTTONUP, WM_MBUTTONDBLCLK = 0x0207, 0x0208, 0x0209
WM_MOUSEWHEEL, WM_MOUSEHWHEEL = 0x020A, 0x020E
WM_KEYDOWN, WM_KEYUP, WM_CHAR, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0102, 0x0104, 0x0105
MK_LBUTTON, MK_RBUTTON, MK_SHIFT, MK_CONTROL, MK_MBUTTON = 0x0001, 0x0002, 0x0004, 0x0008, 0x0010
WHEEL_DELTA = 120

PW_CLIENTONLY, PW_RENDERFULLCONTENT = 0x1, 0x2
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS, BI_RGB = 0, 0

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK = 0x0001, 0x8000, 0x4000
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP = 0x0020, 0x0040
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, KEYEVENTF_SCANCODE = 0x1, 0x2, 0x4, 0x8
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79
SW_RESTORE = 9
CWP_SKIPINVISIBLE, CWP_SKIPDISABLED, CWP_SKIPTRANSPARENT = 0x1, 0x2, 0x4
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MAPVK_VK_TO_VSC = 0

VK = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "shift": 0x10, "ctrl": 0x11, "alt": 0x12,
    "pause": 0x13, "capslock": 0x14, "hangul": 0x15, "hanja": 0x19, "esc": 0x1B, "space": 0x20,
    "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "printscreen": 0x2C, "insert": 0x2D, "delete": 0x2E, "meta": 0x5B, "contextmenu": 0x5D,
    "numlock": 0x90, "scrolllock": 0x91,
    ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF, "`": 0xC0,
    "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
    **{f"f{i}": 0x6F + i for i in range(1, 25)},
}
# 확장 키(스캔코드 앞에 E0 가 붙는 키)
EXTENDED_VK = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2C, 0x2D, 0x2E, 0x5B, 0x5D, 0x90, 0x15, 0x19}


def vk_for(key: str) -> int:
    if key in VK:
        return VK[key]
    if len(key) == 1:
        c = key.upper()
        if "A" <= c <= "Z" or "0" <= c <= "9":
            return ord(c)
    raise NotSupportedError(f"Windows 가상 키로 바꿀 수 없는 키입니다: {key!r} (문자는 type 액션을 쓰세요)")


def make_lparam(x: int, y: int) -> int:
    """마우스 메시지 lParam: 하위 16비트 x, 상위 16비트 y (부호 있는 16비트)."""
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


def key_lparam(scan: int, extended: bool, up: bool, alt: bool = False) -> int:
    lp = 1 | ((scan & 0xFF) << 16)
    if extended:
        lp |= 1 << 24
    if alt:
        lp |= 1 << 29
    if up:
        lp |= (1 << 30) | (1 << 31)
    return lp


# ---------------------------------------------------------------------- ctypes 구조체
ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


# ---------------------------------------------------------------------- Win32 함수 바인딩
_api: Any = None


def _win32() -> Any:
    """Win32 함수를 64비트 핸들이 잘리지 않도록 argtypes/restype 을 지정해 한 번만 바인딩합니다."""
    global _api
    if _api is not None:
        return _api
    if sys.platform != "win32":
        raise BackendError("window 백엔드는 Windows 에서만 동작합니다 (Linux 는 x11 백엔드를 쓰세요)")

    class API:
        pass

    a = API()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    H, B, INT, U = wintypes.HWND, wintypes.BOOL, ctypes.c_int, wintypes.UINT
    a.WNDENUMPROC = ctypes.WINFUNCTYPE(B, H, wintypes.LPARAM)

    def bind(dll: Any, name: str, res: Any, *args: Any) -> None:
        f = getattr(dll, name)
        f.restype = res
        f.argtypes = list(args)
        setattr(a, name, f)

    bind(user32, "EnumWindows", B, a.WNDENUMPROC, wintypes.LPARAM)
    bind(user32, "IsWindow", B, H)
    bind(user32, "IsWindowVisible", B, H)
    bind(user32, "IsIconic", B, H)
    bind(user32, "GetWindowTextLengthW", INT, H)
    bind(user32, "GetWindowTextW", INT, H, wintypes.LPWSTR, INT)
    bind(user32, "GetClassNameW", INT, H, wintypes.LPWSTR, INT)
    bind(user32, "GetWindowThreadProcessId", wintypes.DWORD, H, ctypes.POINTER(wintypes.DWORD))
    bind(user32, "GetClientRect", B, H, ctypes.POINTER(wintypes.RECT))
    bind(user32, "ClientToScreen", B, H, ctypes.POINTER(wintypes.POINT))
    bind(user32, "ScreenToClient", B, H, ctypes.POINTER(wintypes.POINT))
    bind(user32, "ChildWindowFromPointEx", H, H, wintypes.POINT, U)
    bind(user32, "GetDC", wintypes.HDC, H)
    bind(user32, "ReleaseDC", INT, H, wintypes.HDC)
    bind(user32, "PrintWindow", B, H, wintypes.HDC, U)
    bind(user32, "PostMessageW", B, H, U, wintypes.WPARAM, wintypes.LPARAM)
    bind(user32, "SendInput", U, U, ctypes.POINTER(INPUT), INT)
    bind(user32, "GetSystemMetrics", INT, INT)
    bind(user32, "SetForegroundWindow", B, H)
    bind(user32, "GetForegroundWindow", H)
    bind(user32, "ShowWindow", B, H, INT)
    bind(user32, "BringWindowToTop", B, H)
    bind(user32, "MapVirtualKeyW", U, U, U)
    bind(user32, "AttachThreadInput", B, wintypes.DWORD, wintypes.DWORD, B)
    bind(kernel32, "GetCurrentThreadId", wintypes.DWORD)
    bind(kernel32, "OpenProcess", wintypes.HANDLE, wintypes.DWORD, B, wintypes.DWORD)
    bind(kernel32, "CloseHandle", B, wintypes.HANDLE)
    bind(kernel32, "QueryFullProcessImageNameW", B, wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
         ctypes.POINTER(wintypes.DWORD))
    bind(gdi32, "CreateCompatibleDC", wintypes.HDC, wintypes.HDC)
    bind(gdi32, "CreateCompatibleBitmap", wintypes.HBITMAP, wintypes.HDC, INT, INT)
    bind(gdi32, "SelectObject", wintypes.HGDIOBJ, wintypes.HDC, wintypes.HGDIOBJ)
    bind(gdi32, "DeleteObject", B, wintypes.HGDIOBJ)
    bind(gdi32, "DeleteDC", B, wintypes.HDC)
    bind(gdi32, "BitBlt", B, wintypes.HDC, INT, INT, INT, INT, wintypes.HDC, INT, INT, wintypes.DWORD)
    bind(gdi32, "GetDIBits", INT, wintypes.HDC, wintypes.HBITMAP, U, U, ctypes.c_void_p,
         ctypes.POINTER(BITMAPINFO), U)

    # 고해상도(DPI 배율) 화면에서 좌표가 어긋나지 않게: Per-Monitor v2 → v1 → 시스템 순으로 시도
    try:
        user32.SetProcessDpiAwarenessContext.restype = B
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        if not user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            raise OSError
    except (AttributeError, OSError):
        try:
            ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
        except Exception:
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass
    _api = a
    return a


def _process_name(api: Any, pid: int) -> str:
    h = api.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if api.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value.replace("/", "\\").split("\\")[-1]
        return ""
    finally:
        api.CloseHandle(h)


def list_windows() -> list[dict[str, Any]]:
    """보이는 최상위 창 목록 (hwnd, 제목, 클래스, 프로세스, 크기)."""
    api = _win32()
    out: list[dict[str, Any]] = []

    @api.WNDENUMPROC
    def cb(hwnd: int, _lp: int) -> bool:
        if not api.IsWindowVisible(hwnd):
            return True
        n = api.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        title = ctypes.create_unicode_buffer(n + 1)
        api.GetWindowTextW(hwnd, title, n + 1)
        cls = ctypes.create_unicode_buffer(256)
        api.GetClassNameW(hwnd, cls, 256)
        pid = wintypes.DWORD()
        api.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rc = wintypes.RECT()
        api.GetClientRect(hwnd, ctypes.byref(rc))
        out.append({"hwnd": int(hwnd), "title": title.value, "class": cls.value, "pid": pid.value,
                    "process": _process_name(api, pid.value), "width": rc.right - rc.left,
                    "height": rc.bottom - rc.top, "minimized": bool(api.IsIconic(hwnd))})
        return True

    api.EnumWindows(cb, 0)
    return out


class WindowsBackend(Backend):
    name = "window"

    def __init__(self, title: str | None = None, process: str | None = None, class_name: str | None = None,
                 hwnd: int | str | None = None, input_mode: str = "post", window_timeout: float = 10.0,
                 restore_minimized: bool = True, **kw: Any) -> None:
        super().__init__(**kw)
        if input_mode not in ("post", "sendinput"):
            raise BackendError("input_mode 는 post 또는 sendinput 이어야 합니다")
        self.title_pattern = title
        self.process_name = process
        self.class_name = class_name
        self.hwnd = int(hwnd, 0) if isinstance(hwnd, str) else hwnd
        self.input_mode = input_mode
        self.window_timeout = window_timeout
        self.restore_minimized = restore_minimized
        self._lock = threading.Lock()
        self._title = ""
        self._post_buttons = 0  # post 모드에서 눌린 버튼 MK_* 플래그
        self._capture_hwnd = 0  # post 모드: 버튼을 누른 자식 창(드래그 중엔 계속 이 창으로 보냄)
        self._focus_hwnd = 0  # post 모드: 마지막으로 클릭한 자식 창(키 입력 대상)

    # ------------------------------------------------------------------ 창 찾기
    async def start(self) -> None:
        api = _win32()
        deadline = time.time() + self.window_timeout
        while True:
            hwnd = self._find()
            if hwnd:
                break
            if time.time() > deadline:
                raise BackendError(self._not_found_msg())
            await asyncio.sleep(0.5)
        self.hwnd = hwnd
        if api.IsIconic(hwnd) and self.restore_minimized:
            api.ShowWindow(hwnd, SW_RESTORE)
            await asyncio.sleep(0.3)
        log.info("창 선택: hwnd=%#x %r (입력 방식: %s)", hwnd, self._title, self.input_mode)

    def _not_found_msg(self) -> str:
        cond = ", ".join(f"{k}={v!r}" for k, v in (("title", self.title_pattern), ("process", self.process_name),
                                                   ("class", self.class_name), ("hwnd", self.hwnd)) if v)
        return f"조건에 맞는 창을 찾지 못했습니다 ({cond}). 'vmonitor windows' 로 창 목록을 확인하세요"

    def _find(self) -> int | None:
        api = _win32()
        if self.hwnd:
            return self.hwnd if api.IsWindow(self.hwnd) else None
        pat = re.compile(self.title_pattern, re.IGNORECASE) if self.title_pattern else None
        best: tuple[int, dict[str, Any]] | None = None
        me = os.getpid()
        for w in list_windows():
            # vmonitor 를 실행한 콘솔 창(제목에 명령줄이 들어감)이나 뷰어 탭이 대상으로 잡히지 않게 제외
            if w["pid"] == me or "vmonitor" in w["title"].lower():
                continue
            if pat and not pat.search(w["title"]):
                continue
            if self.process_name and w["process"].lower() != self.process_name.lower() \
                    and w["process"].lower() != self.process_name.lower() + ".exe":
                continue
            if self.class_name and w["class"] != self.class_name:
                continue
            area = w["width"] * w["height"]
            if best is None or area > best[0]:
                best = (area, w)
        if best is None:
            return None
        self._title = best[1]["title"]
        return best[1]["hwnd"]

    def _check(self) -> int:
        api = _win32()
        if not self.hwnd or not api.IsWindow(self.hwnd):
            raise BackendError("대상 창이 닫혔습니다")
        return self.hwnd

    # ------------------------------------------------------------------ 캡처
    async def capture(self) -> Image.Image:
        return await asyncio.to_thread(self._capture_sync)

    def _capture_sync(self) -> Image.Image:
        api = _win32()
        hwnd = self._check()
        if api.IsIconic(hwnd):
            raise BackendError("창이 최소화되어 있어 캡처할 수 없습니다 (최소화 대신 다른 창 뒤에 두세요)")
        rc = wintypes.RECT()
        api.GetClientRect(hwnd, ctypes.byref(rc))
        w, h = rc.right - rc.left, rc.bottom - rc.top
        if w <= 0 or h <= 0:
            raise BackendError("창 크기가 0 입니다")
        hdc = api.GetDC(hwnd)
        memdc = api.CreateCompatibleDC(hdc)
        bmp = api.CreateCompatibleBitmap(hdc, w, h)
        old = api.SelectObject(memdc, bmp)
        try:
            if not api.PrintWindow(hwnd, memdc, PW_CLIENTONLY | PW_RENDERFULLCONTENT):
                api.BitBlt(memdc, 0, 0, w, h, hdc, 0, 0, SRCCOPY)
            api.SelectObject(memdc, old)
            bmi = BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = w
            bmi.bmiHeader.biHeight = -h  # 위→아래 순서
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = BI_RGB
            buf = ctypes.create_string_buffer(w * h * 4)
            if api.GetDIBits(memdc, bmp, 0, h, buf, ctypes.byref(bmi), DIB_RGB_COLORS) != h:
                raise BackendError("GetDIBits 실패")
        finally:
            api.DeleteObject(bmp)
            api.DeleteDC(memdc)
            api.ReleaseDC(hwnd, hdc)
        self._size = (w, h)
        return Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1)

    # ------------------------------------------------------------------ 공통 보조
    def _to_screen(self, x: int, y: int) -> tuple[int, int]:
        api = _win32()
        pt = wintypes.POINT(x, y)
        api.ClientToScreen(self._check(), ctypes.byref(pt))
        return pt.x, pt.y

    def _target_at(self, x: int, y: int) -> tuple[int, int, int]:
        """(x,y) 아래의 가장 깊은 자식 창과 그 창 기준 좌표. 메시지는 실제로 그 자식 창에 가야 합니다."""
        api = _win32()
        top = self._check()
        hwnd, cx, cy = top, x, y
        for _ in range(16):
            child = api.ChildWindowFromPointEx(hwnd, wintypes.POINT(cx, cy),
                                               CWP_SKIPINVISIBLE | CWP_SKIPDISABLED | CWP_SKIPTRANSPARENT)
            if not child or child == hwnd:
                break
            sx, sy = self._to_screen(x, y)
            pt = wintypes.POINT(sx, sy)
            api.ScreenToClient(child, ctypes.byref(pt))
            hwnd, cx, cy = child, pt.x, pt.y
        return hwnd, cx, cy

    def _post_target(self, x: int, y: int) -> tuple[int, int, int]:
        """post 모드 마우스 메시지 대상. 버튼을 누른 채면 실제 Windows 의 마우스 캡처처럼 누른 창으로 계속 보냅니다."""
        api = _win32()
        if self._post_buttons and self._capture_hwnd and api.IsWindow(self._capture_hwnd):
            sx, sy = self._to_screen(x, y)
            pt = wintypes.POINT(sx, sy)
            api.ScreenToClient(self._capture_hwnd, ctypes.byref(pt))
            return self._capture_hwnd, pt.x, pt.y
        return self._target_at(x, y)

    def _key_target(self) -> int:
        api = _win32()
        if self._focus_hwnd and api.IsWindow(self._focus_hwnd):
            return self._focus_hwnd
        return self._target_at(*self._cursor)[0]

    def _mk_flags(self) -> int:
        f = self._post_buttons
        if "shift" in self._keys_down:
            f |= MK_SHIFT
        if "ctrl" in self._keys_down:
            f |= MK_CONTROL
        return f

    def _target_is_foreground(self, hwnd: int) -> bool:
        """대상 창(또는 대상 앱이 띄운 대화상자 등 같은 프로세스의 창)이 맨 앞인지."""
        api = _win32()
        fg = api.GetForegroundWindow()
        if not fg:
            return False
        if fg == hwnd:
            return True
        fg_pid, target_pid = wintypes.DWORD(), wintypes.DWORD()
        api.GetWindowThreadProcessId(fg, ctypes.byref(fg_pid))
        api.GetWindowThreadProcessId(hwnd, ctypes.byref(target_pid))
        return fg_pid.value == target_pid.value

    def _foreground(self) -> None:
        """sendinput 모드: 대상 창을 맨 앞으로 가져오고, 실제로 앞에 왔는지 확인합니다.

        Windows 는 포그라운드 잠금으로 창 전환을 거부할 수 있습니다. 확인 없이 입력하면 키 입력이 사용자가 쓰던
        다른 창으로 들어가므로, 끝내 전환되지 않으면 입력하지 않고 오류를 냅니다.
        """
        api = _win32()
        hwnd = self._check()
        if self._target_is_foreground(hwnd):
            return
        if api.IsIconic(hwnd):
            api.ShowWindow(hwnd, SW_RESTORE)
        cur = api.GetCurrentThreadId()
        for attempt in range(3):
            if attempt:
                # 잠금 해제: 우리 프로세스가 '마지막 입력'의 주체가 되면 SetForegroundWindow 가 허용됩니다.
                # 제자리(0,0) 상대 이동이라 화면·커서에는 영향이 없습니다.
                self._send(self._mouse_input(MOUSEEVENTF_MOVE))
            fg = api.GetForegroundWindow()
            fg_thread = api.GetWindowThreadProcessId(fg, None) if fg else 0
            attached = bool(fg_thread and fg_thread != cur and api.AttachThreadInput(cur, fg_thread, True))
            try:
                api.BringWindowToTop(hwnd)
                api.SetForegroundWindow(hwnd)
            finally:
                if attached:
                    api.AttachThreadInput(cur, fg_thread, False)
            deadline = time.time() + 0.5
            while time.time() < deadline:
                if self._target_is_foreground(hwnd):
                    time.sleep(0.03)
                    return
                time.sleep(0.02)
        raise BackendError("대상 창을 앞으로 가져오지 못했습니다 (Windows 포그라운드 잠금). "
                           "다른 창에 입력되지 않도록 입력을 중단했습니다")

    def _send(self, *inputs: INPUT) -> None:
        api = _win32()
        arr = (INPUT * len(inputs))(*inputs)
        n = api.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
        if n != len(inputs):
            raise BackendError(f"SendInput 이 차단되었습니다 (관리자 권한 앱이면 vmonitor 도 관리자 권한으로 실행하세요, "
                               f"err={ctypes.get_last_error()})")

    @staticmethod
    def _mouse_input(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> INPUT:
        return INPUT(type=INPUT_MOUSE, u=_INPUTUNION(mi=MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, 0)))

    @staticmethod
    def _key_input(vk: int, scan: int, flags: int) -> INPUT:
        return INPUT(type=INPUT_KEYBOARD, u=_INPUTUNION(ki=KEYBDINPUT(vk, scan, flags, 0, 0)))

    async def _run(self, fn: Any, *args: Any) -> Any:
        def run() -> Any:
            with self._lock:
                return fn(*args)
        return await asyncio.to_thread(run)

    # ------------------------------------------------------------------ 원시 입력
    async def _move(self, x: int, y: int) -> None:
        await self._run(self._move_sync, x, y)

    def _move_sync(self, x: int, y: int) -> None:
        api = _win32()
        if self.input_mode == "post":
            target, cx, cy = self._post_target(x, y)
            api.PostMessageW(target, WM_MOUSEMOVE, self._mk_flags(), make_lparam(cx, cy))
            return
        self._foreground()
        sx, sy = self._to_screen(x, y)
        vx, vy = api.GetSystemMetrics(SM_XVIRTUALSCREEN), api.GetSystemMetrics(SM_YVIRTUALSCREEN)
        vw, vh = api.GetSystemMetrics(SM_CXVIRTUALSCREEN), api.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        nx = round((sx - vx) * 65535 / max(1, vw - 1))
        ny = round((sy - vy) * 65535 / max(1, vh - 1))
        self._send(self._mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny))

    async def _button(self, button: str, down: bool, click_count: int = 1) -> None:
        await self._run(self._button_sync, button, down, click_count)

    def _button_sync(self, button: str, down: bool, click_count: int) -> None:
        api = _win32()
        if self.input_mode == "post":
            msgs = {"left": (WM_LBUTTONDOWN, WM_LBUTTONUP, WM_LBUTTONDBLCLK, MK_LBUTTON),
                    "right": (WM_RBUTTONDOWN, WM_RBUTTONUP, WM_RBUTTONDBLCLK, MK_RBUTTON),
                    "middle": (WM_MBUTTONDOWN, WM_MBUTTONUP, WM_MBUTTONDBLCLK, MK_MBUTTON)}[button]
            target, cx, cy = self._post_target(*self._cursor)
            if down:
                if not self._post_buttons:
                    self._capture_hwnd = target
                self._focus_hwnd = target
                self._post_buttons |= msgs[3]
                # 실제 입력에서는 시스템이 두 번째 누름을 DBLCLK 로 바꿔 줍니다. 직접 보낼 때는 우리가 바꿉니다.
                msg = msgs[2] if click_count == 2 else msgs[0]
            else:
                self._post_buttons &= ~msgs[3]
                msg = msgs[1]
            api.PostMessageW(target, msg, self._mk_flags(), make_lparam(cx, cy))
            if not self._post_buttons:
                self._capture_hwnd = 0
            return
        self._foreground()
        flag = {("left", True): MOUSEEVENTF_LEFTDOWN, ("left", False): MOUSEEVENTF_LEFTUP,
                ("right", True): MOUSEEVENTF_RIGHTDOWN, ("right", False): MOUSEEVENTF_RIGHTUP,
                ("middle", True): MOUSEEVENTF_MIDDLEDOWN, ("middle", False): MOUSEEVENTF_MIDDLEUP}[(button, down)]
        self._send(self._mouse_input(flag))

    async def _wheel(self, dx: int, dy: int) -> None:
        await self._run(self._wheel_sync, dx, dy)

    def _wheel_sync(self, dx: int, dy: int) -> None:
        api = _win32()
        # Windows 휠: 양수 = 위로(멀어지게). 우리 규약은 dy>0 = 아래로 이므로 부호를 뒤집습니다.
        v, hz = -dy * WHEEL_DELTA, dx * WHEEL_DELTA
        if self.input_mode == "post":
            target, _cx, _cy = self._post_target(*self._cursor)
            sx, sy = self._to_screen(*self._cursor)  # 휠 메시지의 lParam 은 화면 좌표
            if v:
                api.PostMessageW(target, WM_MOUSEWHEEL, ((v & 0xFFFF) << 16) | self._mk_flags(), make_lparam(sx, sy))
            if hz:
                api.PostMessageW(target, WM_MOUSEHWHEEL, ((hz & 0xFFFF) << 16) | self._mk_flags(), make_lparam(sx, sy))
            return
        self._foreground()
        if v:
            self._send(self._mouse_input(MOUSEEVENTF_WHEEL, data=v))
        if hz:
            self._send(self._mouse_input(MOUSEEVENTF_HWHEEL, data=hz))

    async def _key(self, key: str, down: bool) -> None:
        await self._run(self._key_sync, key, down)

    def _key_sync(self, key: str, down: bool) -> None:
        api = _win32()
        vk = vk_for(key)
        scan = api.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
        ext = vk in EXTENDED_VK
        if self.input_mode == "post":
            # Alt 가 눌린 상태(Ctrl 없이)의 키는 WM_SYSKEY* 로 보내야 메뉴 단축키 등이 동작합니다.
            sys_key = ("alt" in self._keys_down or key == "alt") and "ctrl" not in self._keys_down and key != "ctrl"
            if down:
                msg = WM_SYSKEYDOWN if sys_key else WM_KEYDOWN
            else:
                msg = WM_SYSKEYUP if sys_key else WM_KEYUP
            api.PostMessageW(self._key_target(), msg, vk, key_lparam(scan, ext, not down, sys_key))
            return
        self._foreground()
        # 게임(DirectInput)도 인식하도록 스캔코드 기반으로 보냅니다.
        flags = KEYEVENTF_SCANCODE | (KEYEVENTF_EXTENDEDKEY if ext else 0) | (0 if down else KEYEVENTF_KEYUP)
        self._send(self._key_input(vk, scan, flags))

    async def _type(self, text: str) -> None:
        await self._run(self._type_sync, text)

    def _type_sync(self, text: str) -> None:
        api = _win32()
        units = text.replace("\r\n", "\r").replace("\n", "\r").encode("utf-16-le")
        codes = [int.from_bytes(units[i:i + 2], "little") for i in range(0, len(units), 2)]
        if self.input_mode == "post":
            target = self._key_target()
            for c in codes:
                api.PostMessageW(target, WM_CHAR, c, 1)
            return
        self._foreground()
        for c in codes:
            self._send(self._key_input(0, c, KEYEVENTF_UNICODE),
                       self._key_input(0, c, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))

    def info(self) -> dict[str, Any]:
        return {"hwnd": hex(self.hwnd) if self.hwnd else None, "title": self._title, "input_mode": self.input_mode}
