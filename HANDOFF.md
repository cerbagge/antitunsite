# HANDOFF — 웹맵(Dynmap/LiveAtlas) 플레이어 위치 읽기 (VMonitor 1.2.0)

- 저장소: `cerbagge/antitunsite`
- 브랜치: `claude/player-location-handoff` (내용 동일: `claude/gallant-faraday-vf00ch`, PR: https://github.com/cerbagge/antitunsite/pull/2)
- 대상 사이트: `https://map.planetearth.kr/#world;flat;0,64,32;0`

## 1. 무엇을 만들었나

VMonitor(브라우저·앱 화면을 송출하고 AI·매크로가 조작하게 하는 게이트웨이)의 browser 백엔드에 아래 기능을 추가했습니다.

- **응답 감시**: 실제 크롬이 페이지를 여는 동안, 페이지가 스스로 받아 오는 XHR/fetch JSON 응답을 기록합니다.
  JSON 주소를 직접 요청하면 안티봇에 막히는 사이트도, 브라우저가 챌린지를 통과한 뒤 받은 데이터는 읽을 수 있습니다.
  서버에 요청을 추가로 보내지 않습니다.
- **플레이어 위치**: 기록된 응답에서 `players` 목록을 찾아 공통 형식(name, world, x, y, z, health …)으로 돌려줍니다.
  Dynmap/LiveAtlas, squaremap, BlueMap 형식을 지원합니다.
  - 조회 방법: `GET /api/players`, `vmonitor players`, MCP `players` 도구
- **안티봇 대응 옵션**
  - `--stealth`: 자동화 표시 숨김
  - `--user-agent`: User-Agent 변경
  - `--header '이름: 값'`: 모든 요청에 헤더 추가
- **기타**: `GET /api/captures`(응답 기록), `POST /api/eval`(페이지 안 JavaScript 실행)

> 주의: 이 기능은 클라우드 환경에서 대상 사이트에 접속할 수 없어 **로컬 가짜 지도 서버로만 검증**했습니다.
> 실제 사이트의 응답 형식이 다르면 `players` 가 0명으로 나올 수 있습니다(아래 6절 참고).

## 2. 필요한 런타임·의존성

| 항목 | 버전/설명 |
|---|---|
| Windows 10/11 | PowerShell 5.1 이상 |
| Python | 3.10 이상 (`py --version` 으로 확인) |
| Git | 저장소 받기용 |
| Google Chrome | 설치된 크롬을 사용합니다(`--channel chrome`) |
| Python 패키지 | `pip install -e ".[browser,mcp]"` 가 설치: fastapi, uvicorn, pillow, httpx, playwright, mcp |

## 3. 설치 (PowerShell)

```powershell
git clone https://github.com/cerbagge/antitunsite.git
cd antitunsite
git checkout claude/player-location-handoff

py -m venv .venv
# 실행 정책 오류가 나면 먼저: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -e ".[browser,mcp]"
# 설치된 Chrome 대신 Playwright 내장 Chromium 을 쓸 때만 필요:
# python -m playwright install chromium

vmonitor --version   # 예상: vmonitor 1.2.0
```

## 4. 실행 (PowerShell)

**창 1 — 지도 열기(서버):**

```powershell
.\.venv\Scripts\Activate.ps1
vmonitor serve --backend browser `
  --url "https://map.planetearth.kr/#world;flat;0,64,32;0" `
  --channel chrome --headful --user-data-dir .\map_profile --stealth
```

- 크롬 창이 열립니다. 안티봇 챌린지가 뜨면 **처음 한 번만 그 창에서 직접 통과**해 주십시오.
  통과한 쿠키는 `.\map_profile` 에 남아 다음부터는 통과된 상태로 열립니다.
- 브라우저 화면은 `http://127.0.0.1:8765/` 웹 뷰어에서도 볼 수 있습니다.

**창 2 — 플레이어 위치 보기:**

```powershell
.\.venv\Scripts\Activate.ps1
vmonitor players              # 한 번 출력
vmonitor players --watch 5    # 5초마다 계속 출력 (Ctrl+C 종료)
vmonitor players --json       # JSON 한 줄 출력
# 또는
Invoke-RestMethod http://127.0.0.1:8765/api/players
```

**(선택) Claude Code 에서 MCP 도구로 쓰기:**

```powershell
claude mcp add vmonitor -- vmonitor mcp --server http://127.0.0.1:8765
# 도구: players, page_data, evaluate_js, screenshot, click ...
```

## 5. 환경변수·설정값

| 이름 | 필수 | 설명 |
|---|---|---|
| `VMONITOR_TOKEN` | 선택 | 서버 접속 토큰. 서버를 `--host 0.0.0.0` 으로 외부에 공개할 때는 **반드시** 지정 (값은 직접 정하는 비밀값) |
| `VMONITOR_URL` | 선택 | `players`/`mcp` 명령이 붙을 서버 주소 (기본 `http://127.0.0.1:8765`) |
| `--header '이름: 값'` | 선택 | 사이트 운영자가 허용 토큰·헤더를 준 경우 서버 실행 시 추가 (값은 비밀값) |
| `--watch 정규식` | 선택 | 기록할 응답 URL 만 지정 (예: `--watch "/up/world/"`). 생략하면 모든 JSON 응답을 최근 50개까지 기록 |
| `--port` | 선택 | 기본 8765 |

## 6. 예상 출력

서버 시작 시(창 1):

```
  VMonitor 1.2.0  (browser)
  뷰어      http://127.0.0.1:8765/
  영상      http://127.0.0.1:8765/stream.mjpg
  API 문서  http://127.0.0.1:8765/docs
```

`vmonitor players` (플레이어 데이터를 받은 경우):

```
[11:02:15] 2명  (출처 https://map.planetearth.kr/up/world/world/1791850000, 1.4초 전)
  Steve  world  (10.5, 64, -3)
  Alex  world  (120, 70, 455.2)
```

아직 데이터가 없을 때(페이지 로드·챌린지 직후):

```
아직 플레이어 목록이 담긴 응답을 받지 못했습니다(페이지 로드·챌린지 통과 후 몇 초 기다려 주세요)
```

`Invoke-RestMethod http://127.0.0.1:8765/api/players` 응답 형식:

```json
{"source": "https://.../up/world/world/...", "ts": 1791850000.1, "age_s": 1.4, "count": 1,
 "players": [{"name": "Steve", "account": "Steve", "world": "world", "x": 10.5, "y": 64.0, "z": -3.0,
              "health": 20, "armor": 0, "foreign": null}]}
```

**0명으로만 나올 때 점검:** 페이지가 실제로 받는 데이터 주소를 확인합니다.

```powershell
Invoke-RestMethod "http://127.0.0.1:8765/api/captures?data=false" | ConvertTo-Json -Depth 4
```

목록에 플레이어 데이터로 보이는 주소가 있는데 `players` 가 비어 있으면 형식이 다른 것이므로,
그 응답 내용(`/api/captures?pattern=주소일부`)을 확인해 `vmonitor/backends/browser.py` 의 `find_players()` 를 보완해야 합니다.

## 7. 관련 파일

- `vmonitor/backends/browser.py`: 응답 감시, `players()`, `find_players()`, `--stealth`
- `vmonitor/server.py`: `/api/players`, `/api/captures`, `/api/eval`
- `vmonitor/cli.py`: `serve` 옵션, `players` 명령
- `README.md` 4-5절, `CHANGELOG.md` 1.2.0
- `tests/test_page_data.py`: 실제 Chromium 으로 검증하는 테스트
