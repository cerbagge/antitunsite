"""Windows 앱 종류별 호환성 점검.

여러 종류의 앱(Win32 / WinForms / WPF / Tk / 크롬 기반 Edge / Java Swing)을 실제로 띄워
VMonitor 의 window 백엔드로 다음을 확인하고 결과표를 만듭니다.

  1. 가려진 상태 캡처 : 다른 창(분홍색 덮개 창)이 앞을 가리고 있어도 대상 앱 화면만 캡처되는지
  2. post 입력       : 대상 앱이 뒤에 있는 상태(백그라운드)에서 클릭·한글 타이핑·백스페이스가 들어가는지
  3. sendinput 입력  : 대상 앱을 앞으로 가져와 실제 입력처럼 넣었을 때 들어가는지

사용법 (Windows)
  python tools/compat_windows.py                      # 기본 앱 전부 점검 → compat-out/ 에 결과표·스크린샷
  python tools/compat_windows.py --apps notepad,wpf   # 일부만
  python tools/compat_windows.py --title "카카오톡"     # 내 PC 의 특정 앱: 가려진 상태 캡처만 점검(입력 확인은 수동)

입력 검증 방법: 테스트 앱은 입력 내용을 창 제목에 그대로 보여 주도록 만들어져 있고(메모장은 편집 컨트롤 내용을 직접 읽음),
"ab한글" 입력 → 백스페이스 → "ab한" 이 되었는지 확인합니다.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from ctypes import wintypes
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vmonitor.actions import ActionRunner  # noqa: E402
from vmonitor.backends import windows as w  # noqa: E402

TYPED, BACKSPACED = "ab한글", "ab한"
COVER_TITLE = "VMT-COVER"
COVER_RGB = (255, 0, 255)

_u32 = ctypes.WinDLL("user32", use_last_error=True) if sys.platform == "win32" else None
if _u32 is not None:
    _u32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _u32.SendMessageW.restype = wintypes.LPARAM
    _u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _u32.GetForegroundWindow.restype = wintypes.HWND
    _u32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _u32.BringWindowToTop.argtypes = [wintypes.HWND]
    _ENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _u32.EnumChildWindows.argtypes = [wintypes.HWND, _ENUM, wintypes.LPARAM]


# ---------------------------------------------------------------------- Win32 보조
def window_title(hwnd: int) -> str:
    n = _u32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    _u32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def edit_child_text(top: int) -> str | None:
    found: list[int] = []

    @_ENUM
    def cb(hwnd, _lp):
        cls = ctypes.create_unicode_buffer(256)
        _u32.GetClassNameW(hwnd, cls, 256)
        if cls.value in ("Edit", "RichEditD2DPT", "RICHEDIT50W"):
            found.append(hwnd)
            return False
        return True

    _u32.EnumChildWindows(top, cb, 0)
    if not found:
        return None
    n = _u32.SendMessageW(found[0], 0x000E, 0, 0)  # WM_GETTEXTLENGTH
    buf = ctypes.create_unicode_buffer(n + 1)
    _u32.SendMessageW(found[0], 0x000D, n + 1, ctypes.addressof(buf))  # WM_GETTEXT
    return buf.value


def title_payload(hwnd: int) -> str | None:
    t = window_title(hwnd)
    return t.split("|", 1)[1] if "|" in t else None


def force_foreground(hwnd: int) -> bool:
    """덮개 창을 맨 앞으로 (포그라운드 잠금 해제용 제자리 마우스 이동 포함)."""
    for _ in range(5):
        inp = w.INPUT(type=w.INPUT_MOUSE, u=w._INPUTUNION(mi=w.MOUSEINPUT(0, 0, 0, w.MOUSEEVENTF_MOVE, 0, 0)))
        _u32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(w.INPUT))
        _u32.BringWindowToTop(hwnd)
        _u32.SetForegroundWindow(hwnd)
        time.sleep(0.3)
        if _u32.GetForegroundWindow() == hwnd:
            return True
    return False


def kill_tree(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        pass


# ---------------------------------------------------------------------- 테스트 앱
PS_WINFORMS = r"""
Add-Type -AssemblyName System.Windows.Forms
$f = New-Object System.Windows.Forms.Form
$f.Text = 'VMT-WINFORMS|'
$f.Width = 600; $f.Height = 400
$t = New-Object System.Windows.Forms.TextBox
$t.Multiline = $true; $t.Dock = 'Fill'
$t.Add_TextChanged({ $f.Text = 'VMT-WINFORMS|' + $t.Text })
$f.Controls.Add($t)
[System.Windows.Forms.Application]::Run($f)
"""

PS_WPF = r"""
Add-Type -AssemblyName PresentationFramework
$w = New-Object System.Windows.Window
$w.Title = 'VMT-WPF|'
$w.Width = 600; $w.Height = 400
$tb = New-Object System.Windows.Controls.TextBox
$tb.AcceptsReturn = $true
$tb.Add_TextChanged({ $w.Title = 'VMT-WPF|' + $tb.Text })
$w.Content = $tb
[void]$w.ShowDialog()
"""

PS_COVER = r"""
Add-Type -AssemblyName System.Windows.Forms
$f = New-Object System.Windows.Forms.Form
$f.Text = 'VMT-COVER'
$f.StartPosition = 'Manual'
$f.Left = 0; $f.Top = 0
$f.Width = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds.Width
$f.Height = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds.Height
$f.BackColor = [System.Drawing.Color]::FromArgb(255, 0, 255)
[System.Windows.Forms.Application]::Run($f)
"""

TK_APP = r"""
import tkinter as tk
r = tk.Tk(); r.title('VMT-TK|'); r.geometry('600x400')
e = tk.Text(r); e.pack(fill='both', expand=True)
def tick():
    r.title('VMT-TK|' + e.get('1.0', 'end-1c')); r.after(100, tick)
tick(); r.mainloop()
"""

EDGE_PAGE = """<!doctype html><meta charset="utf-8"><title>VMT-EDGE|</title>
<body style="margin:0"><textarea style="width:100vw;height:100vh;border:0;font-size:24px"
 oninput="document.title='VMT-EDGE|'+this.value"></textarea></body>"""

SWING_APP = r"""
import javax.swing.*; import javax.swing.event.*;
public class VmtSwing {
  public static void main(String[] a) { SwingUtilities.invokeLater(() -> {
    JFrame f = new JFrame("VMT-SWING|"); JTextArea t = new JTextArea();
    t.getDocument().addDocumentListener(new DocumentListener() {
      void u() { f.setTitle("VMT-SWING|" + t.getText()); }
      public void insertUpdate(DocumentEvent e) { u(); } public void removeUpdate(DocumentEvent e) { u(); }
      public void changedUpdate(DocumentEvent e) { u(); } });
    f.add(new JScrollPane(t)); f.setSize(600, 400); f.setDefaultCloseOperation(JFrame.EXIT_ON_CLOSE); f.setVisible(true);
  }); }
}
"""


@dataclass
class AppSpec:
    key: str
    label: str
    find: dict[str, Any]
    launch: Callable[[Path], subprocess.Popen | None]
    readback: Callable[[int], str | None]
    note: str = ""


def _ps(script: str, tmp: Path, name: str) -> subprocess.Popen:
    p = tmp / f"{name}.ps1"
    p.write_text(script, encoding="ascii")
    return subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-STA", "-File", str(p)])


def _edge_path() -> str | None:
    for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if os.path.exists(p):
            return p
    return shutil.which("msedge")


def _launch_edge(tmp: Path) -> subprocess.Popen | None:
    exe = _edge_path()
    if not exe:
        return None
    page = tmp / "vmt-edge.html"
    page.write_text(EDGE_PAGE, encoding="utf-8")
    prof = Path(tempfile.mkdtemp(prefix="vmt-edge-"))
    return subprocess.Popen([exe, f"--user-data-dir={prof}", "--no-first-run", "--no-default-browser-check",
                             "--disable-features=msEdgeFirstRunExperience", "--window-size=600,400",
                             f"--app={page.as_uri()}"])


def _launch_swing(tmp: Path) -> subprocess.Popen | None:
    javac = shutil.which("javac")
    java = shutil.which("java")
    if not javac or not java:
        return None
    src = tmp / "VmtSwing.java"
    src.write_text(SWING_APP, encoding="utf-8")
    subprocess.run([javac, "-encoding", "UTF-8", str(src)], check=True, cwd=tmp, capture_output=True)
    return subprocess.Popen([java, "-cp", str(tmp), "VmtSwing"])


APPS = [
    AppSpec("notepad", "메모장 (Win32)", {"process": "notepad.exe"},
            lambda tmp: subprocess.Popen(["notepad.exe"]), edit_child_text),
    AppSpec("winforms", "WinForms", {"title": r"^VMT-WINFORMS\|"},
            lambda tmp: _ps(PS_WINFORMS, tmp, "winforms"), title_payload),
    AppSpec("wpf", "WPF", {"title": r"^VMT-WPF\|"}, lambda tmp: _ps(PS_WPF, tmp, "wpf"), title_payload),
    AppSpec("tk", "Tkinter (Python)", {"title": r"^VMT-TK\|"},
            lambda tmp: subprocess.Popen([sys.executable, "-c", TK_APP]), title_payload),
    AppSpec("edge", "Edge (크롬 기반)", {"title": r"^VMT-EDGE\|"}, _launch_edge, title_payload),
    AppSpec("swing", "Java Swing", {"title": r"^VMT-SWING\|"}, _launch_swing, title_payload),
]


# ---------------------------------------------------------------------- 점검
@dataclass
class Result:
    app: str
    label: str
    mode: str
    launched: bool = False
    background: bool | None = None
    capture_ok: bool | None = None
    capture_note: str = ""
    input_ok: bool | None = None
    text: str | None = None
    error: str = ""
    screenshot: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def capture_check(img: Any) -> tuple[bool, str]:
    if img.getextrema() == ((0, 0), (0, 0), (0, 0)):
        return False, "전부 검은색"
    small = img.resize((min(200, img.width), min(150, img.height)))
    px = list(small.getdata())
    cover = sum(1 for p in px if abs(p[0] - 255) < 30 and p[1] < 40 and abs(p[2] - 255) < 30) / len(px)
    if cover > 0.2:
        return False, f"덮개 창이 찍힘({cover:.0%})"
    return True, f"{img.width}x{img.height}"


async def check(spec: AppSpec, mode: str, tmp: Path, out: Path, cover_hwnd: int | None) -> Result:
    res = Result(spec.key, spec.label, mode)
    proc = None
    b = None
    try:
        proc = spec.launch(tmp)
        if proc is None:
            res.error = "이 PC 에 해당 앱이 없음"
            return res
        b = w.WindowsBackend(input_mode=mode, window_timeout=40, input_delay=0.02, **spec.find)
        await b.start()
        res.launched = True
        await asyncio.sleep(2.0)  # 앱 초기화
        if mode == "post" and cover_hwnd:
            res.background = force_foreground(cover_hwnd)
        img = await b.capture()
        res.capture_ok, res.capture_note = capture_check(img)
        shot = out / f"{spec.key}-{mode}.png"
        img.save(shot)
        res.screenshot = shot.name
        runner = ActionRunner(b)
        r = await runner.run([
            {"action": "click", "x": img.width // 2, "y": img.height // 2},
            {"action": "type", "text": TYPED},
            {"action": "key", "keys": "backspace"},
        ])
        bad = [x for x in r if not x["ok"]]
        if bad:
            res.error = bad[0].get("error", "")
        deadline = time.time() + 4
        while time.time() < deadline:
            res.text = spec.readback(b.hwnd)
            if res.text == BACKSPACED:
                break
            await asyncio.sleep(0.2)
        res.input_ok = res.text == BACKSPACED
        if mode == "post" and cover_hwnd:
            res.extra["foreground_after"] = "cover" if _u32.GetForegroundWindow() == cover_hwnd else "other"
    except Exception as e:
        res.error = f"{type(e).__name__}: {e}"
        traceback.print_exc()
    finally:
        if b is not None:
            await b.stop()
        kill_tree(proc)
        await asyncio.sleep(0.5)
    return res


async def check_title(pattern: str, out: Path, cover_hwnd: int | None) -> Result:
    res = Result("custom", f"사용자 지정: {pattern}", "capture-only")
    b = w.WindowsBackend(title=pattern, window_timeout=10)
    try:
        await b.start()
        res.launched = True
        if cover_hwnd:
            res.background = force_foreground(cover_hwnd)
        img = await b.capture()
        res.capture_ok, res.capture_note = capture_check(img)
        shot = out / "custom-capture.png"
        img.save(shot)
        res.screenshot = shot.name
    except Exception as e:
        res.error = f"{type(e).__name__}: {e}"
    return res


def mark(v: bool | None) -> str:
    return "✅" if v else ("❌" if v is False else "—")


def to_markdown(results: list[Result]) -> str:
    rows = {}
    for r in results:
        rows.setdefault((r.app, r.label), {})[r.mode] = r
    lines = ["| 앱 | 가려진 상태 캡처 | post (백그라운드) 입력 | sendinput (실제 입력) | 비고 |",
             "|---|---|---|---|---|"]
    for (_key, label), modes in rows.items():
        p, s, c = modes.get("post"), modes.get("sendinput"), modes.get("capture-only")
        cap = p or c
        notes = []
        for name, r in (("post", p), ("sendinput", s), ("capture", c)):
            if r is None:
                continue
            if r.error:
                notes.append(f"{name}: {r.error}")
            elif r.input_ok is False:
                notes.append(f"{name} 결과={r.text!r}")
        if p is not None and p.background is False:
            notes.append("덮개 창을 앞으로 못 가져옴(백그라운드 조건 미충족)")
        lines.append(f"| {label} | {mark(cap.capture_ok) if cap else '—'} {cap.capture_note if cap else ''} | "
                     f"{mark(p.input_ok) if p else '—'} | {mark(s.input_ok) if s else '—'} | "
                     f"{'; '.join(notes).replace('|', '/') or ''} |")
    return "\n".join(lines)


async def main_async(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="vmt-"))
    cover_proc = _ps(PS_COVER, tmp, "cover")
    cover_hwnd = None
    for _ in range(60):
        hits = [x for x in w.list_windows() if x["title"] == COVER_TITLE]
        if hits:
            cover_hwnd = hits[0]["hwnd"]
            break
        await asyncio.sleep(0.5)
    print(f"덮개 창: {hex(cover_hwnd) if cover_hwnd else '없음'}", flush=True)
    results: list[Result] = []
    try:
        if args.title:
            results.append(await check_title(args.title, out, cover_hwnd))
        else:
            wanted = set(args.apps.split(",")) if args.apps else None
            for spec in APPS:
                if wanted and spec.key not in wanted:
                    continue
                for mode in ("post", "sendinput"):
                    print(f"== {spec.label} / {mode}", flush=True)
                    r = await check(spec, mode, tmp, out, cover_hwnd)
                    print(f"   capture={r.capture_ok} ({r.capture_note}) input={r.input_ok} text={r.text!r} "
                          f"bg={r.background} err={r.error}", flush=True)
                    results.append(r)
    finally:
        kill_tree(cover_proc)
    md = to_markdown(results)
    (out / "report.md").write_text(md + "\n", encoding="utf-8")
    (out / "report.json").write_text(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    print("\n" + md, flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## VMonitor Windows 앱 호환성\n\n" + md + "\n\n"
                    f"입력 검증: `{TYPED}` 입력 → 백스페이스 → `{BACKSPACED}` 확인. "
                    "post 는 분홍색 덮개 창이 앞에 있는 상태(대상 앱이 뒤)에서 실행.\n")
    return 0


def main() -> None:
    if sys.platform != "win32":
        sys.exit("Windows 에서만 실행할 수 있습니다")
    ap = argparse.ArgumentParser(description="VMonitor Windows 앱 호환성 점검")
    ap.add_argument("--out", default="compat-out", help="결과 폴더")
    ap.add_argument("--apps", help="일부 앱만: " + ",".join(a.key for a in APPS))
    ap.add_argument("--title", help="내 PC 의 특정 앱 창 제목(정규식): 가려진 상태 캡처만 점검")
    sys.exit(asyncio.run(main_async(ap.parse_args())))


if __name__ == "__main__":
    main()
