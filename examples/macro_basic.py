"""기본 매크로 예제 - 데모 화면에서 버튼을 누르고, 입력하고, 색이 바뀌길 기다립니다.

    vmonitor serve --backend demo          # 다른 터미널에서
    python examples/macro_basic.py
"""

from vmonitor.client import MonitorClient

m = MonitorClient("http://127.0.0.1:8765")  # 토큰을 썼다면 token="..."
print("화면 크기:", m.size())

# 1) 좌표 클릭 (좌표는 웹 뷰어의 '좌표 확인' 모드로 쉽게 알 수 있습니다)
m.click(150, 120)

# 2) 버튼이 빨간색(#ef4444)으로 바뀔 때까지 최대 5초 대기
if m.wait_for_color(150, 120, "#ef4444", timeout=5):
    print("버튼 A 가 눌렸습니다")

# 3) 입력칸 클릭 → 한글 입력 → 엔터
m.click(400, 225)
m.type("안녕하세요 VMonitor")
m.key("enter")

# 4) 여러 동작을 한 번에 보내고, 끝난 화면을 바로 받기
r = m.actions([
    {"action": "double_click", "x": 480, "y": 120},
    {"action": "scroll", "x": 480, "y": 400, "direction": "down", "amount": 3},
], screenshot={"format": "png", "max_width": 640})
print("결과:", [x["ok"] for x in r["results"]], "스크린샷", r["screenshot"]["width"], "x", r["screenshot"]["height"])

m.screenshot_image().save("after_macro.png")
print("after_macro.png 저장")
