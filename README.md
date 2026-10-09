# VMonitor — 가상 모니터 브릿지

[![CI](https://github.com/cerbagge/antitunsite/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/cerbagge/antitunsite/actions/workflows/ci.yml)

> 특정 **앱 창이나 브라우저를 '모니터'처럼 송출**하고, 그 화면을 **다른 AI·매크로가 보고**
> **실제 PC처럼 마우스·키보드 입력**을 보내 조작할 수 있게 해 주는 게이트웨이입니다.

```
 ┌──────────────┐  캡처   ┌────────────────────────────┐  화면(PNG/JPEG/MJPEG)  ┌──────────────────────┐
 │ 대상          │ ──────▶ │  VMonitor 서버               │ ─────────────────────▶ │ 보는 쪽 / 조작하는 쪽   │
 │ - 브라우저 탭  │         │  · 프레임 허브(한 번 캡처,     │                        │ - 웹 뷰어(사람)          │
 │ - Windows 창  │ ◀────── │    여러 시청자에게 분배)       │ ◀───────────────────── │ - 매크로(Python/HTTP)   │
 │ - Xvfb 가상    │  입력   │  · 액션 실행기(클릭·키·타이핑)  │  액션(JSON)            │ - AI (MCP / Claude)     │
 │   모니터 앱    │         │  · REST / WebSocket / MCP     │                        │ - OpenCV / VLC / OBS    │
 └──────────────┘         └────────────────────────────┘                        └──────────────────────┘
```

- **모니터처럼 송출**: MJPEG 실시간 영상(`/stream.mjpg`) → 브라우저, OpenCV, VLC, OBS 에서 바로 재생
- **AI가 보는 화면**: 스크린샷 API(축소·좌표 격자·커서 표시), MCP 도구 서버, Claude 컴퓨터 사용 에이전트 내장
- **실제 PC 같은 입력**: 이동·클릭·더블/트리플클릭·우클릭·드래그·휠·단축키·키 누르고 있기·한글 타이핑
- **좌표 자동 환산**: AI가 축소된 스크린샷을 보고 준 좌표를 실제 화면 좌표로 변환 (`space`)
- **사람용 웹 뷰어**: 화면 보기 + 직접 조작 + 클릭 지점의 좌표·색 확인(매크로 만들 때 편리)

---

## 1. 지원하는 송출 대상(백엔드)

| 백엔드 | 대상 | 입력 방식 | 실제 마우스를 빼앗나? | 비고 |
|---|---|---|---|---|
| `browser` | Playwright 로 띄운 브라우저 탭 (Chromium/Firefox/WebKit) | 브라우저 내부(CDP) 입력 | **아니오** (headless 가능) | 가장 안정적. 여러 개 동시 실행 가능. 로그인 유지(`--user-data-dir`) |
| `window` | **Windows 의 특정 앱 창** (메모장, 카카오톡, 게임 등) | `post`: 창에 메시지 직접 전달 / `sendinput`: 실제 하드웨어 입력 | `post`: **아니오** / `sendinput`: 예 | 창이 다른 창에 가려져도 캡처됨(최소화는 불가) |
| `x11` | Linux X 디스플레이의 창, 또는 **Xvfb 가상 모니터**에서 실행한 앱 | XTest (가짜 하드웨어 입력) | 가상 모니터면 **아니오** | `--xvfb --launch "앱"` 으로 전용 모니터 생성 |
| `demo` | 내장 테스트 화면 | 메모리 | 아니오 | 설치 직후 동작 확인·테스트용 |

> **Windows `post` vs `sendinput`**
> - `post`(기본): PC 를 계속 쓰면서 백그라운드로 조작합니다. 실제 커서·포커스를 건드리지 않습니다.
>   게임(DirectInput/Raw Input), WPF·Tk 앱, Ctrl 조합 단축키는 앱에 따라 무시될 수 있습니다(아래 실측표).
> - `--post-activate`: `post` 보완. 입력 직전 대상 앱에 '활성화됨' 메시지만 보내 Java(Swing) 앱 등도 백그라운드로 받게 합니다.
> - `sendinput`: 창을 맨 앞으로 가져와 진짜 키보드·마우스처럼 입력합니다. 거의 모든 앱(게임 포함)에서 동작하지만
>   입력하는 순간 실제 커서·포커스가 움직입니다. 관리자 권한으로 실행된 앱은 VMonitor 도 관리자 권한이어야 합니다.
>   창 전환이 거부되면 다른 창에 입력되지 않도록 입력을 중단하고 오류를 냅니다.

### Windows 앱 종류별 실측 결과

GitHub Actions 의 Windows Server 러너에서 `tools/compat_windows.py` 로 측정했습니다 (2026-10-09, 커밋 `2925744`).
분홍색 덮개 창이 앞을 가린 상태에서 캡처하고, `ab한글` 입력 → 백스페이스 → `ab한` 이 되는지 확인했습니다.
post 계열은 입력 후에도 덮개 창이 계속 앞에 있었습니다(= 실제 포커스를 빼앗지 않음).

| 앱 종류 | 가려진 상태 캡처 | `post` | `post` + `--post-activate` | `sendinput` |
|---|---|---|---|---|
| 메모장 (Win32) | ✅ | ✅ | ✅ | ✅ |
| WinForms | ✅ | ✅ | ✅ | ✅ |
| Edge (크롬 기반) | ✅ | ✅ | ✅ | ✅ |
| Java Swing | ✅ | ❌ | ✅ | ✅ |
| WPF | ✅ | ❌ | ❌ | ✅ |
| Tkinter | ✅ | ❌ | ❌ | ✅ |

**고르는 순서**: `post` → 안 되면 `--post-activate` → 그래도 안 되면 `--input-mode sendinput`.

**내 PC 에서 점검하기** (Windows):

```bash
python tools/compat_windows.py                       # 위 6종을 내 PC 에서 똑같이 점검 → compat-out/report.md
python tools/compat_windows.py --title "카카오톡"      # 내가 쓸 앱: 가려진 상태 캡처 확인 + compat-out/custom-capture.png
```

## 2. 설치

Python 3.10 이상이 필요합니다.

```bash
git clone https://github.com/cerbagge/antitunsite.git
cd antitunsite
pip install -e ".[all]"          # 브라우저·MCP·Claude 에이전트·(Linux)X11 까지 전부
playwright install chromium      # browser 백엔드를 쓸 때 한 번
```

필요한 것만 설치하려면: `pip install -e .` (기본: demo·window 백엔드 + 서버) 에
`[browser]`, `[x11]`, `[mcp]`, `[agent]` 를 골라 붙입니다. Linux 가상 모니터는 `sudo apt install xvfb` 도 필요합니다.

## 3. 빠른 시작

```bash
# 1) 데모 화면으로 동작 확인
vmonitor serve --backend demo
#  → 브라우저로 http://127.0.0.1:8765/ 접속 → '조작 허용'을 켜고 클릭·타이핑해 보기

# 2) 웹사이트를 모니터처럼 송출 (화면 없는 브라우저)
vmonitor serve --backend browser --url https://www.google.com --width 1280 --height 800

# 3) Windows 의 특정 앱 창 송출
vmonitor windows                                   # 창 목록 확인
vmonitor serve --backend window --title "메모장|Notepad"
vmonitor serve --backend window --process notepad.exe --input-mode sendinput

# 4) Linux 가상 모니터에서 앱 실행 + 송출
vmonitor serve --backend x11 --xvfb --width 1280 --height 800 --launch "firefox" --title "Firefox"
```

서버를 켜면 주소가 출력됩니다.

| 주소 | 용도 |
|---|---|
| `http://127.0.0.1:8765/` | 웹 뷰어 (실시간 화면 · 직접 조작 · 좌표/색 확인 · 스크린샷 저장) |
| `http://127.0.0.1:8765/stream.mjpg` | 실시간 영상 (VLC/OBS/OpenCV 입력으로 사용) |
| `http://127.0.0.1:8765/docs` | API 자동 문서 (Swagger) |

### 주요 옵션 (`vmonitor serve -h`)

| 옵션 | 설명 |
|---|---|
| `--host 0.0.0.0 --token 비밀값` | 다른 PC 에서 접속 허용 + 토큰 인증 (**외부 공개 시 토큰 필수**) |
| `--fps 15` | 시청자가 있을 때 캡처 속도 (아무도 안 보면 캡처를 멈춤) |
| `--view-only` | 송출만 하고 입력은 차단 |
| `--input-delay-ms 10` | 입력 이벤트 사이 간격 (느린 앱이면 늘리기) |
| `--headful`, `--user-data-dir ./profile`, `--channel chrome` | (browser) 창 띄우기, 로그인 유지, 설치된 크롬/엣지 사용 |
| `--title`, `--process`, `--class-name`, `--hwnd`, `--input-mode` | (window) 대상 창 지정, 입력 방식 |
| `--xvfb`, `--launch`, `--display`, `--title`, `--window-id` | (x11) 가상 모니터, 실행할 앱, 대상 창 |

## 4. AI·매크로 연동 방법

### 4-1. HTTP API (어떤 언어든)

```bash
# 화면 보기 (AI 용: 최대 1280px 로 축소 + 100px 격자)
curl -o screen.png "http://127.0.0.1:8765/api/screenshot?max_width=1280&grid=100"

# 입력 보내기 + 끝난 화면 받기
curl -X POST http://127.0.0.1:8765/api/actions -H "Content-Type: application/json" -d '{
  "actions": [
    {"action": "click", "x": 640, "y": 360},
    {"action": "type",  "text": "안녕하세요"},
    {"action": "key",   "keys": "enter"}
  ],
  "screenshot": {"format": "jpeg", "max_width": 1280}
}'
```

**액션 규격** (좌표 기본값 = 실제 화면 픽셀, 왼쪽 위가 0,0)

| action | 필드 | 예 |
|---|---|---|
| `move` | `x, y` | `{"action":"move","x":10,"y":20}` |
| `click` | `x?, y?, button(left/right/middle), count, modifiers` | `{"action":"click","x":10,"y":20,"modifiers":"ctrl"}` |
| `double_click` / `triple_click` / `right_click` / `middle_click` | `x?, y?` | |
| `mouse_down` / `mouse_up` | `button, x?, y?` | 직접 누르고 떼기 |
| `drag` | `from:[x,y], to:[x,y]` 또는 `path:[[x,y],...]`, `steps` | `{"action":"drag","from":[10,10],"to":[200,80]}` |
| `scroll` | `x?, y?, dy(+아래)/dx(+오른쪽)` 또는 `direction, amount` | `{"action":"scroll","x":500,"y":400,"dy":3}` |
| `key` | `keys`, `repeat` — 공백으로 여러 개 | `"ctrl+c"`, `"alt+tab"`, `"ctrl+a backspace"` |
| `key_down` / `key_up` | `key` | `{"action":"key_down","key":"shift"}` |
| `hold_key` | `keys, duration(초)` | 게임 이동키 등 |
| `type` | `text, interval_ms` | 한글·이모지 포함 문자열 |
| `wait` | `ms` 또는 `duration(초)` | |
| `navigate` | `url` (browser 전용) | |
| `cursor_position` | — | 현재 커서 좌표 반환 |

- 키 이름은 `enter/esc/tab/backspace/delete/home/end/pageup/pagedown/up/down/left/right/space/f1~f24/ctrl/alt/shift/meta(win)/hangul/hanja` 와
  xdotool 표기(`Return`, `Page_Down`, `super`), 웹 표기(`Enter`, `ArrowLeft`, `Control`) 를 모두 인식합니다.
- **좌표계 환산**: AI 가 480×300 으로 축소된 화면을 보고 좌표를 줬다면 요청에 `"space": [480, 300]` 을 넣으면 됩니다.
  `"space": "norm"` 이면 0~1 비율 좌표입니다.
- 묶음 중 하나가 실패하면 나머지는 실행하지 않고, 눌린 키·버튼은 자동으로 떼어 줍니다.
- Claude 컴퓨터 사용 도구 형식(`left_click`, `coordinate`, `scroll_direction` …)도 그대로 받습니다.

**WebSocket** `ws://127.0.0.1:8765/ws?frames=true&fps=10`
- 보내기: `{"id": 1, "actions": [...]}` → 받기: `{"id": 1, "ok": true, "results": [...]}`
- `frames=true` 이면 JPEG 프레임이 바이너리 메시지로 계속 들어옵니다.

### 4-2. Python 매크로

```python
from vmonitor.client import MonitorClient

m = MonitorClient("http://127.0.0.1:8765")
m.click(150, 120)
m.wait_for_color(150, 120, "#ef4444", timeout=5)   # 그 점이 빨간색이 될 때까지 대기
m.type("안녕하세요"); m.key("enter")
m.drag(100, 100, 300, 200)
img = m.screenshot_image()                          # PIL 이미지 → OpenCV 등으로 분석
```

예제: [`examples/macro_basic.py`](examples/macro_basic.py) (기본 매크로),
[`examples/image_search_click.py`](examples/image_search_click.py) (이미지 서치 후 클릭),
[`examples/opencv_stream.py`](examples/opencv_stream.py) (실시간 영상 분석).

명령줄로도 바로 보낼 수 있습니다.

```bash
vmonitor do '[{"action":"type","text":"안녕"},{"action":"key","keys":"enter"}]'
vmonitor shot -o now.png --grid 100
```

### 4-3. MCP (Claude Desktop · Claude Code · Cursor 등)

`vmonitor serve ...` 를 켜 둔 상태에서 MCP 클라이언트에 등록합니다.

```bash
# Claude Code
claude mcp add vmonitor -- vmonitor mcp --server http://127.0.0.1:8765
```

```json
// Claude Desktop: claude_desktop_config.json
{
  "mcpServers": {
    "vmonitor": { "command": "vmonitor", "args": ["mcp", "--server", "http://127.0.0.1:8765"] }
  }
}
```

제공 도구: `screenshot`, `screen_info`, `click`, `move_mouse`, `drag`, `scroll`, `type_text`, `press_key`, `navigate`, `wait`, `run_actions`.
AI 에게는 최대 1280×800 으로 축소한 스크린샷을 보여 주고, AI 가 준 좌표는 실제 화면 좌표로 자동 환산합니다.

### 4-4. Claude 컴퓨터 사용 에이전트 (내장)

Claude 가 화면을 보고 스스로 클릭·입력하며 작업을 끝까지 수행합니다.

```bash
export ANTHROPIC_API_KEY=sk-ant-...            # Windows: set ANTHROPIC_API_KEY=...
vmonitor serve --backend browser --url https://ko.wikipedia.org &
vmonitor agent "검색창에 '대한민국'을 검색하고 첫 문단을 요약해 줘"
vmonitor agent "..." --confirm                 # 동작 묶음마다 사람이 승인
```

- 모델 기본값 `claude-opus-5-5`, 컴퓨터 사용 도구 `computer_toolset_20260801`, 사고(adaptive thinking) 노력 수준 기본 `medium` (`--effort` 로 변경).
- 안전 분류기가 요청을 거절하면 **서버 측 대체 모델(`fallbacks: "default"`)로 자동 재시도**하도록 켜 두었습니다. 끄려면 `--no-fallback`.
- 진행 상황 요약(`display: "updates"`)을 터미널에 표시합니다.

## 5. 보안 주의

- 기본은 `127.0.0.1`(내 PC)에서만 접속됩니다. `--host 0.0.0.0` 으로 공개할 때는 **반드시 `--token`** 을 지정하세요
  (토큰 없이 공개하면 같은 네트워크의 누구나 그 앱을 조작할 수 있습니다).
- 토큰은 `Authorization: Bearer <토큰>` 헤더, `X-API-Key` 헤더, 또는 `?token=` 으로 전달합니다.
- AI 에이전트에게 결제·삭제·메시지 전송 같은 되돌리기 어려운 작업을 맡길 때는 `--confirm` 을 권장합니다.

## 6. 프로젝트 구조

```
vmonitor/
  cli.py              명령줄 (serve / windows / shot / do / mcp / agent)
  server.py           HTTP·WebSocket 게이트웨이, MJPEG 스트림
  hub.py              프레임 허브 (캡처 1회 → 다수 시청자, 인코딩 캐시, 격자/커서 표시)
  actions.py          액션 규격·검증·실행 (좌표계 환산, 실패 시 중단·키 해제)
  keys.py             키 이름 정규화 (xdotool/웹/별칭 → 정규 이름)
  client.py           Python 매크로 클라이언트
  mcp_server.py       MCP 도구 서버
  agents/claude_agent.py  Claude 컴퓨터 사용 에이전트 루프
  backends/
    base.py           백엔드 공통 인터페이스 (원시 동작 → 클릭·드래그·단축키 조합)
    browser.py        Playwright 브라우저
    windows.py        Win32 창 캡처(PrintWindow) + 입력(PostMessage/SendInput)
    x11.py            X11/Xvfb 캡처 + XTest 입력
    demo.py           내장 데모 화면
  web/viewer.html     웹 뷰어
tests/                pytest (키·액션·서버·브라우저·X11·Windows·MCP·에이전트)
.github/workflows/    CI (린트·패키지, Linux, Windows)
examples/             매크로·이미지 서치·영상 분석 예제
```

테스트: `pip install -e ".[all,dev]" && pytest` (린트: `ruff check .`)

CI(GitHub Actions)는 PR·`main` 푸시마다 실행됩니다.

| 작업 | 내용 |
|---|---|
| 린트 · 패키지 | `ruff check`, wheel 빌드 후 설치해 웹 뷰어 포함 여부 확인 |
| 테스트 (Linux, Python 3.10 / 3.13) | 전체 테스트 + Xvfb 가상 모니터 안 Chromium 앱 창 입력 + 브라우저 백엔드 |
| 테스트 (Windows) | 전체 테스트 + **메모장을 실제로 띄워 캡처·입력(post / sendinput) 확인** |

CI 에서는 `VMONITOR_STRICT_TESTS=1` 이 켜져 있어 통합 테스트가 환경 문제로 건너뛰어지면 실패로 처리됩니다.

## 7. 알려진 제한

- Windows 창이 **최소화**되면 Windows 가 화면을 그리지 않아 캡처할 수 없습니다(다른 창 뒤에 두는 것은 괜찮음).
- Windows `post` 모드는 게임·일부 앱에서 입력이 무시될 수 있습니다 → `--input-mode sendinput`.
- 보호된 콘텐츠(DRM 영상), 일부 안티치트 게임은 캡처·입력이 차단될 수 있습니다.
- 브라우저 백엔드의 `hangul`/`hanja` 키는 지원하지 않습니다 → 한글은 `type` 액션으로 입력하세요.
- 데모 화면의 한글은 시스템 한글 글꼴(맑은 고딕/나눔/Noto CJK)이 있어야 제대로 그려집니다.
