"""Windows 창 백엔드 통합 테스트 (Windows 에서만).

메모장을 실제로 띄워 ① 창 찾기 ② 클라이언트 영역 캡처 ③ 입력(post / sendinput 두 방식)이
편집 컨트롤에 실제로 들어가는지 WM_GETTEXT 로 확인합니다.
"""

import asyncio
import ctypes
import os
import shutil
import subprocess
import sys
import time

import pytest

if sys.platform != "win32":
    pytest.skip("Windows 전용", allow_module_level=True)

from ctypes import wintypes  # noqa: E402

from vmonitor.actions import ActionRunner  # noqa: E402
from vmonitor.backends import windows as w  # noqa: E402

WM_GETTEXT, WM_GETTEXTLENGTH = 0x000D, 0x000E
EDIT_CLASSES = ("Edit", "RichEditD2DPT", "RICHEDIT50W")

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.SendMessageW.restype = wintypes.LPARAM
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_ENUMCHILD = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_user32.EnumChildWindows.argtypes = [wintypes.HWND, _ENUMCHILD, wintypes.LPARAM]
_user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_user32.OpenInputDesktop.restype = wintypes.HANDLE
_user32.CloseDesktop.argtypes = [wintypes.HANDLE]


def edit_child(top: int) -> int | None:
    found: list[int] = []

    @_ENUMCHILD
    def cb(hwnd, _lp):
        cls = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(hwnd, cls, 256)
        if cls.value in EDIT_CLASSES:
            found.append(hwnd)
            return False
        return True

    _user32.EnumChildWindows(top, cb, 0)
    return found[0] if found else None


def edit_text(top: int) -> str:
    child = edit_child(top)
    assert child, "메모장 편집 컨트롤을 찾지 못했습니다"
    n = _user32.SendMessageW(child, WM_GETTEXTLENGTH, 0, 0)
    buf = ctypes.create_unicode_buffer(n + 1)
    _user32.SendMessageW(child, WM_GETTEXT, n + 1, ctypes.addressof(buf))
    return buf.value


def wait_text(top: int, expected: str, timeout: float = 5.0) -> str:
    deadline = time.time() + timeout
    text = edit_text(top)
    while text != expected and time.time() < deadline:
        time.sleep(0.1)
        text = edit_text(top)
    return text


# 스토어판 메모장은 강제 종료돼도 탭 내용을 복원하므로 테스트 사이에 상태를 지웁니다(고전 메모장에선 영향 없음).
_STORE_NOTEPAD_STATE = os.path.expandvars(
    r"%LOCALAPPDATA%\Packages\Microsoft.WindowsNotepad_8wekyb3d8bbwe\LocalState\TabState")


@pytest.fixture
def notepad():
    shutil.rmtree(_STORE_NOTEPAD_STATE, ignore_errors=True)
    proc = subprocess.Popen(["notepad.exe"])
    yield proc
    proc.kill()
    subprocess.run(["taskkill", "/F", "/IM", "notepad.exe"], capture_output=True)
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        pass
    shutil.rmtree(_STORE_NOTEPAD_STATE, ignore_errors=True)
    time.sleep(0.5)


def test_struct_and_helpers():
    assert ctypes.sizeof(w.INPUT) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
    assert w.vk_for("a") == 0x41 and w.vk_for("enter") == 0x0D and w.vk_for("f5") == 0x74
    assert w.make_lparam(3, 2) == (2 << 16) | 3
    assert isinstance(w.list_windows(), list)


async def _type_into_notepad(input_mode: str, text: str) -> tuple[tuple[int, int], int]:
    b = w.WindowsBackend(process="notepad.exe", input_mode=input_mode, window_timeout=30, input_delay=0.02)
    await b.start()
    try:
        img = await b.capture()
        assert img.size[0] > 100 and img.size[1] > 100, img.size
        assert img.getextrema() != ((0, 0), (0, 0), (0, 0)), "캡처가 전부 검은색입니다"
        width, height = img.size
        r = ActionRunner(b)
        res = await r.run([
            {"action": "click", "x": width // 2, "y": height // 2},
            {"action": "type", "text": text},
            {"action": "key", "keys": "backspace"},
        ])
        assert all(x["ok"] for x in res), res
        return img.size, b.hwnd
    finally:
        await b.stop()


def test_notepad_post_mode(notepad):
    """백그라운드 메시지 방식: 창에 직접 입력 (실제 마우스·포커스를 쓰지 않음)."""
    _, hwnd = asyncio.run(_type_into_notepad("post", "post 안녕"))
    assert wait_text(hwnd, "post 안") == "post 안"


def test_notepad_sendinput_mode(notepad, unavailable):
    """실제 입력 방식: 창을 앞으로 가져와 하드웨어 입력처럼 넣음."""
    desk = _user32.OpenInputDesktop(0, False, 0x0100)
    if not desk:
        unavailable("대화형 데스크톱이 없어 SendInput 을 시험할 수 없습니다")
    _user32.CloseDesktop(desk)
    _, hwnd = asyncio.run(_type_into_notepad("sendinput", "send 한글"))
    assert wait_text(hwnd, "send 한") == "send 한"
