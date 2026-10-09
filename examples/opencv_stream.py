"""MJPEG 실시간 영상을 OpenCV 로 받아 보는 예제 (영상 분석·녹화 파이프라인의 시작점).

    pip install opencv-python
    python examples/opencv_stream.py

같은 주소(http://127.0.0.1:8765/stream.mjpg)를 VLC '네트워크 스트림 열기'나 OBS '미디어 소스'에 넣어도 됩니다.
"""

import cv2

URL = "http://127.0.0.1:8765/stream.mjpg?fps=15"  # 토큰을 썼다면 &token=...

cap = cv2.VideoCapture(URL)
if not cap.isOpened():
    raise SystemExit(f"스트림을 열 수 없습니다: {URL}")
while True:
    ok, frame = cap.read()
    if not ok:
        break
    cv2.imshow("VMonitor", frame)
    if cv2.waitKey(1) & 0xFF in (27, ord("q")):  # Esc 또는 q 로 종료
        break
cap.release()
cv2.destroyAllWindows()
