"""Claude 컴퓨터 사용(computer use) 에이전트.

Claude 가 VMonitor 화면을 '모니터'로 보고, 컴퓨터 사용 도구(``computer_toolset_20260801``)로
클릭·타이핑·스크롤 등을 지시하면 이 루프가 VMonitor 서버에 그대로 실행합니다.

    export ANTHROPIC_API_KEY=...
    vmonitor serve --backend browser --url https://www.wikipedia.org &
    vmonitor agent "검색창에 '대한민국'을 입력하고 검색 결과 첫 문단을 요약해 줘"

- 스크린샷은 최대 1280x800 으로 줄여 보내고, Claude 가 준 좌표는 서버가 실제 화면 좌표로 환산합니다.
- 안전 분류기가 요청을 거절하면 서버 측 대체 모델(fallbacks="default")로 자동 재시도합니다(--no-fallback 으로 끔).
- --confirm 을 주면 각 동작 묶음을 실행하기 전에 사람에게 확인을 받습니다.
"""

from __future__ import annotations

import base64
import io
import time
from typing import Any

from ..client import MonitorClient, MonitorError

TOOLSET = "computer"

SYSTEM_PROMPT = """You control one application window or browser tab through screenshots streamed by VMonitor.
The screenshot is the entire screen you can act on: there is no desktop, taskbar, or other application outside it.
Coordinates are pixels in the most recent full screenshot. Check the result of actions that change the screen
before moving on. When the task is done or cannot be done, stop and briefly report the outcome in the user's language."""


class ComputerExecutor:
    """Claude 의 computer 도구 호출을 VMonitor 액션으로 실행합니다."""

    def __init__(self, monitor: MonitorClient, max_width: int = 1280, max_height: int = 800,
                 settle_ms: int = 400) -> None:
        self.m = monitor
        self.max_width = max_width
        self.max_height = max_height
        self.settle_ms = settle_ms
        self.view: tuple[int, int] | None = None

    def screenshot_block(self) -> dict[str, Any]:
        data, meta = self.m.screenshot(fmt="png", max_width=self.max_width, max_height=self.max_height)
        self.view = (meta["width"], meta["height"])
        return {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                            "data": base64.b64encode(data).decode()}}

    def zoom_block(self, region: list[float]) -> dict[str, Any]:
        """region=[x0,y0,x1,y1] (스크린샷 좌표) 부분을 원본 해상도로 잘라 보여 줍니다."""
        from PIL import Image

        if self.view is None:
            self.screenshot_block()
        data, meta = self.m.screenshot(fmt="png")
        img = Image.open(io.BytesIO(data)).convert("RGB")
        sx = meta["frame_width"] / self.view[0]
        sy = meta["frame_height"] / self.view[1]
        x0, y0, x1, y1 = (float(v) for v in region)
        box = (int(max(0, min(x0, x1) * sx)), int(max(0, min(y0, y1) * sy)),
               int(min(img.width, max(x0, x1) * sx)), int(min(img.height, max(y0, y1) * sy)))
        if box[2] - box[0] < 2 or box[3] - box[1] < 2:
            raise ValueError("zoom 영역이 너무 작습니다")
        crop = img.crop(box)
        crop.thumbnail((self.max_width, self.max_height))
        buf = io.BytesIO()
        crop.save(buf, "PNG")
        return {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                            "data": base64.b64encode(buf.getvalue()).decode()}}

    def execute(self, name: str, args: dict[str, Any]) -> list[dict[str, Any]]:
        """도구 하나 실행 → tool_result content 블록 목록."""
        if name == "screenshot":
            return [self.screenshot_block()]
        if name == "zoom":
            return [self.zoom_block(args["region"])]
        if self.view is None:
            self.screenshot_block()
        # VMonitor 액션 규격이 Claude 컴퓨터 도구의 이름·필드(coordinate, text, scroll_direction ...)를 그대로 받습니다.
        action = {"action": name, **args}
        if name in ("left_mouse_down", "left_mouse_up"):
            action["button"] = "left"
        r = self.m.actions([action], space=list(self.view), check=False)
        res = r["results"][0]
        if not res.get("ok"):
            raise RuntimeError(res.get("error", "실패"))
        if name == "cursor_position":
            x, y = res["value"]
            return [{"type": "text", "text": f"X={x},Y={y}"}]
        return [{"type": "text", "text": "OK"}]


def describe(name: str, args: dict[str, Any]) -> str:
    parts = [name]
    for k in ("coordinate", "start_coordinate", "region", "scroll_direction", "scroll_amount", "duration", "repeat"):
        if k in args:
            parts.append(f"{k}={args[k]}")
    if "text" in args:
        t = str(args["text"])
        parts.append(repr(t if len(t) <= 40 else t[:40] + "…"))
    return " ".join(parts)


def run_agent(task: str, server: str = "http://127.0.0.1:8765", token: str | None = None,
              model: str = "claude-opus-5-5", effort: str = "medium", max_steps: int = 40,
              max_width: int = 1280, max_height: int = 800, confirm: bool = False, fallback: bool = True,
              client: Any = None, out: Any = print) -> bool:
    """작업이 끝나면(Claude 가 더 이상 도구를 부르지 않으면) True. 거절·오류·횟수 초과면 False."""
    import anthropic

    api = client or anthropic.Anthropic()
    mon = MonitorClient(server, token=token)
    ex = ComputerExecutor(mon, max_width, max_height)
    try:
        first_shot = ex.screenshot_block()
    except (MonitorError, OSError) as e:
        out(f"VMonitor 서버({server})에 연결할 수 없습니다: {e}")
        return False

    betas = ["thinking-display-updates-2026-08-18"]
    extra: dict[str, Any] = {}
    if fallback:
        betas.append("server-side-fallback-2026-07-01")
        extra["fallbacks"] = "default"
    messages: list[dict[str, Any]] = [{"role": "user", "content": [
        {"type": "text", "text": task},
        {"type": "text", "text": "Current screen:"},
        first_shot,
    ]}]

    for _step in range(1, max_steps + 1):
        try:
            resp = api.beta.messages.create(
                model=model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                tools=[{"type": "computer_toolset_20260801"}],
                thinking={"type": "adaptive", "display": "updates"},
                output_config={"effort": effort},
                betas=betas,
                messages=messages,
                **extra,
            )
        except anthropic.AuthenticationError:
            out("Anthropic API 키가 없거나 올바르지 않습니다. ANTHROPIC_API_KEY 를 설정하세요.")
            return False
        except anthropic.RateLimitError as e:
            out(f"요청 한도 초과 - 잠시 후 다시 시도하세요: {e}")
            return False
        except anthropic.APIStatusError as e:
            out(f"API 오류 {e.status_code}: {e.message}")
            return False
        except anthropic.APIConnectionError as e:
            out(f"Anthropic API 에 연결할 수 없습니다: {e}")
            return False

        messages.append({"role": "assistant", "content": resp.content})
        for block in resp.content:
            if block.type == "text" and block.text.strip():
                out(f"[Claude] {block.text.strip()}")
            elif block.type == "thinking" and getattr(block, "thinking", ""):
                out(f"  · {block.thinking.strip()}")
            elif block.type == "fallback":
                out(f"  (안전 분류기 거절로 {block.to.model} 모델이 이어서 처리합니다)")

        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            out(f"Claude 가 요청을 거절했습니다: {getattr(details, 'category', None) or ''} "
                f"{getattr(details, 'explanation', None) or ''}".strip())
            return False

        calls = [b for b in resp.content if b.type == "tool_use"]
        if not calls:
            if resp.stop_reason == "max_tokens":
                out("응답이 max_tokens 에서 잘렸습니다.")
                return False
            return True

        if confirm:
            out("다음 동작을 실행합니다:")
            for b in calls:
                out(f"   - {describe(b.name, dict(b.input))}")
            approved = input("진행할까요? [y/N] ").strip().lower() in ("y", "yes", "ㅛ")
        else:
            approved = True

        results: list[dict[str, Any]] = []
        failed = False
        saw_image = False
        for b in calls:
            base = {"type": "tool_result", "tool_use_id": b.id, "toolset_name": getattr(b, "toolset_name", None) or TOOLSET}
            if failed:
                results.append({**base, "is_error": True,
                                "content": "Not executed: an earlier computer action in this turn failed."})
                continue
            if not approved:
                results.append({**base, "is_error": True, "content": "The user declined this action."})
                failed = True
                continue
            if getattr(b, "toolset_name", None) != TOOLSET:
                results.append({**base, "is_error": True, "content": f"Error: unknown tool {b.name}"})
                failed = True
                continue
            args = dict(b.input)
            out(f"  → {describe(b.name, args)}")
            try:
                content = ex.execute(b.name, args)
                saw_image = saw_image or b.name in ("screenshot", "zoom")
                results.append({**base, "content": content})
            except Exception as e:
                out(f"    실패: {e}")
                results.append({**base, "is_error": True, "content": f"Error: {e}"})
                failed = True
        # 묶음이 스크린샷으로 끝나지 않았으면 결과 화면을 마지막 결과에 붙여 한 번 왕복을 아낍니다.
        if not failed and not saw_image and approved:
            time.sleep(ex.settle_ms / 1000)
            try:
                last = results[-1]
                last["content"] = [*last["content"], ex.screenshot_block()]
            except Exception:
                pass
        messages.append({"role": "user", "content": results})

    out(f"최대 단계({max_steps})에 도달해 중단했습니다.")
    return False
