# 변경 기록

버전 규칙: `주.부.수` — 주(메인 기능 추가/수정/삭제), 부(중요 버그 수정·덜 중요한 기능 변경), 수(자잘한 버그 수정)

## 1.0.1 — 2026-10-09

### 추가
- GitHub Actions CI (`.github/workflows/ci.yml`): 린트(ruff)·wheel 패키징 확인, Linux(Python 3.10·3.13) 전체 테스트,
  Windows 전체 테스트 + 메모장 실제 캡처·입력(post/sendinput) 통합 테스트
- 테스트 엄격 모드(`VMONITOR_STRICT_TESTS=1`): CI 에서 브라우저·가상 모니터·Windows 통합 테스트가 건너뛰어지면 실패 처리
- ruff 설정을 `pyproject.toml` 로 통일

### 수정
- Python 3.10 에서 MCP `screenshot` 도구가 이미지 직렬화 오류로 실패하던 문제
- Windows 에서 출력을 파일·파이프로 돌릴 때 한글 등 문자 인코딩 오류로 CLI 가 중단될 수 있던 문제

## 1.0.0 — 2026-10-09

첫 공개 버전.

### 추가
- 송출 대상(백엔드) 4종: `browser`(Playwright), `window`(Windows 앱 창), `x11`(Linux/Xvfb 가상 모니터), `demo`
- 게이트웨이 서버: 웹 뷰어, MJPEG 실시간 영상, 스크린샷/픽셀 API, 액션 API, WebSocket(액션 + 프레임)
- 입력 액션: 이동·클릭(단일/더블/트리플/우/가운데)·누르기/떼기·드래그·휠·단축키·키 유지·한글 타이핑·대기·주소 이동
- 좌표계 자동 환산(`space`), 키 이름 정규화(xdotool·웹·별칭 표기), 실패 시 중단 + 눌린 키 자동 해제
- 웹 뷰어: 직접 조작, 좌표·색 확인(매크로 작성용), 격자 표시, 스크린샷 저장, 텍스트 보내기
- AI 연동: Python 클라이언트, MCP 도구 서버, Claude 컴퓨터 사용 에이전트(`vmonitor agent`)
- 보안: 토큰 인증(Bearer / X-API-Key / ?token=), 읽기 전용 모드, 외부 공개 시 경고
- 테스트 39개 (키·액션·서버·브라우저·X11 실제 앱 창·MCP·에이전트 루프)
