"""명령줄 진입점: ``vmonitor <명령>``.

  serve    대상(데모/브라우저/Windows 창/X11)을 송출하는 게이트웨이 서버 실행
  windows  송출할 수 있는 창 목록 보기 (Windows: Win32, Linux: X11)
  shot     실행 중인 서버에서 스크린샷 한 장 저장
  do       실행 중인 서버에 액션(JSON) 보내기
  mcp      Claude Desktop/Claude Code 등 MCP 클라이언트용 도구 서버(stdio) 실행
  agent    Claude(컴퓨터 사용 도구)가 화면을 보고 작업을 수행하게 하기
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any

from . import __version__

DEFAULT_PORT = 8765
DEFAULT_SERVER = f"http://127.0.0.1:{DEFAULT_PORT}"


def _add_client_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--server", default=os.environ.get("VMONITOR_URL", DEFAULT_SERVER),
                   help=f"VMonitor 서버 주소 (기본 {DEFAULT_SERVER}, 환경변수 VMONITOR_URL)")
    p.add_argument("--token", default=os.environ.get("VMONITOR_TOKEN"), help="접속 토큰 (환경변수 VMONITOR_TOKEN)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="vmonitor",
                                 description="앱/브라우저 화면을 모니터처럼 송출하고 AI·매크로가 조작하게 하는 게이트웨이")
    ap.add_argument("--version", action="version", version=f"vmonitor {__version__}")
    ap.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="송출 서버 실행", formatter_class=argparse.RawDescriptionHelpFormatter,
                       epilog="""예시:
  vmonitor serve --backend demo
  vmonitor serve --backend browser --url https://www.google.com --width 1280 --height 800
  vmonitor serve --backend browser --url https://example.com --user-data-dir ./profile --headful
  vmonitor serve --backend window --title "메모장|Notepad"
  vmonitor serve --backend window --process chrome.exe --input-mode sendinput
  vmonitor serve --backend x11 --xvfb --width 1280 --height 800 --launch "firefox" --title Firefox
""")
    s.add_argument("--backend", default="demo", choices=["demo", "browser", "window", "x11"], help="송출 대상 종류")
    s.add_argument("--host", default="127.0.0.1", help="바인딩 주소 (외부 공개 시 0.0.0.0 + --token 필수 권장)")
    s.add_argument("--port", type=int, default=DEFAULT_PORT)
    s.add_argument("--fps", type=float, default=10.0, help="시청자가 있을 때 캡처 프레임 수")
    s.add_argument("--token", default=os.environ.get("VMONITOR_TOKEN"), help="접속 토큰 (없으면 인증 없음)")
    s.add_argument("--view-only", action="store_true", help="화면 송출만 하고 입력은 막기")
    s.add_argument("--input-delay-ms", type=float, default=10.0, help="입력 이벤트 사이 간격(ms)")
    s.add_argument("--name", help="뷰어에 표시할 이름")
    s.add_argument("--width", type=int, default=1280, help="브라우저 뷰포트 / Xvfb 화면 너비 / 데모 너비")
    s.add_argument("--height", type=int, default=800, help="브라우저 뷰포트 / Xvfb 화면 높이 / 데모 높이")
    s.add_argument("--title", help="[window/x11] 창 제목 정규식 (예: \"메모장|Notepad\")")
    g = s.add_argument_group("browser 백엔드")
    g.add_argument("--url", default="about:blank", help="처음 열 주소")
    g.add_argument("--headful", action="store_true", help="브라우저 창을 실제로 띄우기(기본은 화면 없는 headless)")
    g.add_argument("--user-data-dir", help="로그인 상태를 유지할 브라우저 프로필 폴더")
    g.add_argument("--browser", default="chromium", choices=["chromium", "firefox", "webkit"])
    g.add_argument("--channel", help="설치된 브라우저 사용: chrome, msedge 등")
    g.add_argument("--executable-path", help="브라우저 실행 파일 경로")
    g = s.add_argument_group("window 백엔드 (Windows)")
    g.add_argument("--process", help="프로세스 이름 (예: notepad.exe)")
    g.add_argument("--class-name", help="창 클래스 이름")
    g.add_argument("--hwnd", help="창 핸들 (예: 0x1A2B3C)")
    g.add_argument("--input-mode", default="post", choices=["post", "sendinput"],
                   help="post=백그라운드 메시지(마우스 안 뺏음), sendinput=실제 입력(앞으로 가져옴)")
    g = s.add_argument_group("x11 백엔드 (Linux)")
    g.add_argument("--display", help="X 디스플레이 (예: :0, :99)")
    g.add_argument("--xvfb", action="store_true", help="가상 모니터(Xvfb)를 새로 만들어 사용")
    g.add_argument("--launch", help="가상 모니터 안에서 실행할 명령")
    g.add_argument("--window-id", help="창 ID (예: 0x600003)")

    w = sub.add_parser("windows", help="송출 가능한 창 목록")
    w.add_argument("--display", help="[Linux] X 디스플레이")
    w.add_argument("--json", action="store_true")

    sh = sub.add_parser("shot", help="스크린샷 저장")
    _add_client_args(sh)
    sh.add_argument("-o", "--output", default="screenshot.png")
    sh.add_argument("--grid", type=int, help="N 픽셀마다 좌표 눈금")
    sh.add_argument("--max-width", type=int)

    d = sub.add_parser("do", help="액션 보내기", formatter_class=argparse.RawDescriptionHelpFormatter,
                       epilog="""예시:
  vmonitor do '{"action":"click","x":100,"y":200}'
  vmonitor do '[{"action":"type","text":"안녕하세요"},{"action":"key","keys":"enter"}]'
""")
    _add_client_args(d)
    d.add_argument("actions", help="액션 JSON (객체 또는 배열)")

    m = sub.add_parser("mcp", help="MCP 도구 서버 (stdio)")
    _add_client_args(m)
    m.add_argument("--max-width", type=int, default=1280, help="AI 에게 보여줄 스크린샷 최대 너비")
    m.add_argument("--max-height", type=int, default=800, help="AI 에게 보여줄 스크린샷 최대 높이")

    a = sub.add_parser("agent", help="Claude 가 화면을 보고 작업 수행 (ANTHROPIC_API_KEY 필요)")
    _add_client_args(a)
    a.add_argument("task", help="시킬 일 (예: \"검색창에 날씨를 입력하고 검색해 줘\")")
    a.add_argument("--model", default="claude-opus-5-5")
    a.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    a.add_argument("--max-steps", type=int, default=40, help="최대 왕복 횟수")
    a.add_argument("--max-width", type=int, default=1280)
    a.add_argument("--max-height", type=int, default=800)
    a.add_argument("--confirm", action="store_true", help="각 동작 묶음을 실행하기 전에 사람에게 확인받기")
    a.add_argument("--no-fallback", action="store_true", help="안전 분류기 거절 시 대체 모델 재시도 끄기")
    return ap


def main(argv: list[str] | None = None) -> None:
    # Windows 에서 출력을 파일·파이프로 돌리면 cp949/cp1252 로 인코딩되어 일부 문자가 오류를 낼 수 있음 → 대체 문자로 출력
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")  # type: ignore[union-attr]
        except Exception:
            pass
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        stream=sys.stderr)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.cmd == "serve":
        _serve(args)
    elif args.cmd == "windows":
        _windows(args)
    elif args.cmd == "shot":
        from .client import MonitorClient

        c = MonitorClient(args.server, token=args.token)
        data, meta = c.screenshot(grid=args.grid, max_width=args.max_width)
        with open(args.output, "wb") as f:
            f.write(data)
        print(f"{args.output} 저장 ({meta['width']}x{meta['height']}, 원본 {meta['frame_width']}x{meta['frame_height']})")
    elif args.cmd == "do":
        from .client import MonitorClient

        c = MonitorClient(args.server, token=args.token)
        r = c.actions(json.loads(args.actions))
        print(json.dumps(r, ensure_ascii=False, indent=2))
        sys.exit(0 if r.get("ok") else 1)
    elif args.cmd == "mcp":
        from .mcp_server import run_mcp

        run_mcp(args.server, token=args.token, max_width=args.max_width, max_height=args.max_height)
    elif args.cmd == "agent":
        from .agents.claude_agent import run_agent

        ok = run_agent(args.task, server=args.server, token=args.token, model=args.model, effort=args.effort,
                       max_steps=args.max_steps, max_width=args.max_width, max_height=args.max_height,
                       confirm=args.confirm, fallback=not args.no_fallback)
        sys.exit(0 if ok else 1)


def _backend_opts(args: argparse.Namespace) -> dict[str, Any]:
    common = {"input_delay": args.input_delay_ms / 1000}
    if args.backend == "demo":
        return {**common, "width": args.width, "height": args.height}
    if args.backend == "browser":
        return {**common, "url": args.url, "width": args.width, "height": args.height, "headless": not args.headful,
                "user_data_dir": args.user_data_dir, "browser": args.browser, "channel": args.channel,
                "executable_path": args.executable_path}
    if args.backend == "window":
        if not (args.title or args.process or args.class_name or args.hwnd):
            sys.exit("window 백엔드에는 --title / --process / --class-name / --hwnd 중 하나가 필요합니다 "
                     "('vmonitor windows' 로 목록 확인)")
        return {**common, "title": args.title, "process": args.process, "class_name": args.class_name,
                "hwnd": args.hwnd, "input_mode": args.input_mode}
    return {**common, "display": args.display, "xvfb": args.xvfb, "width": args.width, "height": args.height,
            "launch": args.launch, "window": args.title, "window_id": args.window_id}


def _serve(args: argparse.Namespace) -> None:
    import uvicorn

    from .backends import create_backend
    from .server import create_app

    log = logging.getLogger("vmonitor")
    if args.host not in ("127.0.0.1", "localhost", "::1") and not args.token:
        log.warning("⚠ %s 로 외부에 공개하면서 토큰이 없습니다. 같은 네트워크의 누구나 이 화면을 조작할 수 있습니다. "
                    "--token 을 지정하세요.", args.host)
    backend = create_backend(args.backend, **_backend_opts(args))
    app = create_app(backend, fps=args.fps, token=args.token, read_only=args.view_only, title=args.name)
    shown = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    tq = f"?token={args.token}" if args.token else ""
    print(f"\n  VMonitor {__version__}  ({args.backend})\n"
          f"  뷰어      http://{shown}:{args.port}/{tq}\n"
          f"  영상      http://{shown}:{args.port}/stream.mjpg{tq}\n"
          f"  API 문서  http://{shown}:{args.port}/docs\n", file=sys.stderr)
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level.lower())


def _windows(args: argparse.Namespace) -> None:
    if sys.platform == "win32":
        from .backends.windows import list_windows

        rows = list_windows()
    else:
        import asyncio

        from .backends.x11 import X11Backend

        b = X11Backend(display=args.display)
        asyncio.run(b.start())
        rows = b.list_windows()
        asyncio.run(b.stop())
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    for r in rows:
        ident = hex(r["hwnd"]) if "hwnd" in r else r["id"]
        extra = f"  [{r.get('process', '')}]" if r.get("process") else ""
        print(f"{ident:>12}  {r['width']:>5}x{r['height']:<5} {r['title']}{extra}")
