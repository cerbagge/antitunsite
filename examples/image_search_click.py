"""이미지 서치 매크로 예제 - 화면에서 특정 그림(버튼 아이콘 등)을 찾아 그 위치를 클릭합니다.

    pip install opencv-python numpy
    python examples/image_search_click.py button.png

button.png 는 웹 뷰어의 '스크린샷' 버튼으로 저장한 화면에서 원하는 부분만 잘라 만들면 됩니다.
"""

import sys

import cv2
import numpy as np

from vmonitor.client import MonitorClient


def find(screen_bgr: np.ndarray, template_bgr: np.ndarray, threshold: float = 0.9):
    res = cv2.matchTemplate(screen_bgr, template_bgr, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    if score < threshold:
        return None, score
    h, w = template_bgr.shape[:2]
    return (loc[0] + w // 2, loc[1] + h // 2), score


def main() -> None:
    template = cv2.imread(sys.argv[1] if len(sys.argv) > 1 else "button.png")
    if template is None:
        sys.exit("템플릿 이미지를 읽을 수 없습니다")
    m = MonitorClient("http://127.0.0.1:8765")
    screen = cv2.cvtColor(np.array(m.screenshot_image()), cv2.COLOR_RGB2BGR)  # 원본 해상도
    center, score = find(screen, template)
    if center is None:
        print(f"찾지 못했습니다 (최고 유사도 {score:.2f})")
        return
    print(f"찾음: {center} (유사도 {score:.2f}) → 클릭")
    m.click(*center)


if __name__ == "__main__":
    main()
