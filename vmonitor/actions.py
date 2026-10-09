"""입력 액션 규격과 실행기.

AI·매크로는 아래 JSON 객체(또는 그 배열)를 보내 화면을 조작합니다. 좌표는 기본적으로
**송출 화면의 픽셀 좌표**입니다. 축소된 스크린샷을 보고 좌표를 정했다면 요청에
``"space": [너비, 높이]`` 를 함께 보내면 실제 화면 크기로 자동 환산됩니다.
``"units": "norm"`` 을 주면 0~1 비율 좌표로 해석합니다.

=====================  ==========================================================
action                 필드
=====================  ==========================================================
move                   x, y
click                  x?, y?, button=left|right|middle, count=1, modifiers="ctrl+shift"
double_click           x?, y?  (click count=2)
triple_click           x?, y?  (click count=3)
right_click            x?, y?
middle_click           x?, y?
mouse_down / mouse_up  button=left, x?, y?, count=1 (연속 클릭 중 몇 번째인지)
drag                   from=[x,y], to=[x,y]  또는 path=[[x,y],...], button, steps
scroll                 x?, y?, dy=칸수(+아래), dx=칸수(+오른쪽)  또는 direction=up|down|left|right, amount
key                    keys="ctrl+c" (공백으로 여러 개: "ctrl+a backspace"), repeat=1
key_down / key_up      key="shift"
hold_key               keys="shift", duration=초
type                   text="안녕하세요", interval_ms=0
wait                   ms=500  (또는 duration=초)
navigate               url="https://..." (브라우저 백엔드 전용)
cursor_position        (현재 커서 좌표 반환)
screenshot             (아무 것도 하지 않음 - 배치 끝에 스크린샷을 받고 싶을 때 표시용)
=====================  ==========================================================

Claude 컴퓨터 사용 도구의 이름(left_click, left_click_drag, mouse_move, left_mouse_down 등)과
필드(coordinate, start_coordinate, text, scroll_direction, scroll_amount)도 그대로 받습니다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Sequence

from .backends.base import Backend, BackendError
from .keys import Combo, KeyError_, normalize_key, parse_combo, parse_key_sequence, split_modifier_string

log = logging.getLogger(__name__)

MAX_WAIT_SECONDS = 300.0


class ActionError(ValueError):
    """잘못된 액션 요청."""


ALIASES = {
    "mouse_move": "move", "moveto": "move", "move_to": "move", "hover": "move",
    "left_click": "click", "tap": "click",
    "dblclick": "double_click", "doubleclick": "double_click",
    "rightclick": "right_click", "context_click": "right_click",
    "left_mouse_down": "mouse_down", "left_mouse_up": "mouse_up",
    "left_click_drag": "drag", "drag_and_drop": "drag",
    "press": "key", "hotkey": "key", "keypress": "key", "key_press": "key",
    "type_text": "type", "write": "type", "text": "type",
    "sleep": "wait", "pause": "wait",
    "goto": "navigate", "open_url": "navigate",
    "hold": "hold_key",
}

ACTIONS = {
    "move", "click", "double_click", "triple_click", "right_click", "middle_click",
    "mouse_down", "mouse_up", "drag", "scroll", "key", "key_down", "key_up", "hold_key",
    "type", "wait", "navigate", "cursor_position", "screenshot",
}


@dataclass
class Space:
    """요청 좌표계 → 실제 프레임 픽셀 변환."""

    width: float | None = None
    height: float | None = None
    norm: bool = False

    def to_frame(self, x: float, y: float, frame_size: tuple[int, int]) -> tuple[float, float]:
        fw, fh = frame_size
        if self.norm:
            return x * fw, y * fh
        if self.width and self.height and fw and fh:
            return x * fw / self.width, y * fh / self.height
        return x, y

    def from_frame(self, x: float, y: float, frame_size: tuple[int, int]) -> tuple[float, float]:
        fw, fh = frame_size
        if self.norm and fw and fh:
            return x / fw, y / fh
        if self.width and self.height and fw and fh:
            return x * self.width / fw, y * self.height / fh
        return x, y


def parse_space(value: Any) -> Space:
    if value is None:
        return Space()
    if isinstance(value, str) and value.lower() in ("norm", "normalized", "ratio"):
        return Space(norm=True)
    if isinstance(value, dict):
        return Space(float(value["width"]), float(value["height"]))
    if isinstance(value, (list, tuple)) and len(value) == 2:
        w, h = float(value[0]), float(value[1])
        if w <= 0 or h <= 0:
            raise ActionError("space 는 양수 [너비, 높이] 여야 합니다")
        return Space(w, h)
    raise ActionError(f"space 형식이 올바르지 않습니다: {value!r}")


def _num(v: Any, what: str) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ActionError(f"{what} 값이 숫자가 아닙니다: {v!r}") from None


def _point(a: dict[str, Any], *names: str, required: bool = False) -> tuple[float, float] | None:
    """x/y 또는 coordinate=[x,y] 등 여러 표기에서 좌표를 꺼냅니다."""
    for n in names:
        v = a.get(n)
        if v is None or n == "x":
            continue
        if isinstance(v, dict):
            return _num(v.get("x"), f"{n}.x"), _num(v.get("y"), f"{n}.y")
        if isinstance(v, (list, tuple)) and len(v) == 2:
            return _num(v[0], f"{n}[0]"), _num(v[1], f"{n}[1]")
        raise ActionError(f"{n} 는 [x, y] 형식이어야 합니다: {v!r}")
    if "x" in names and a.get("x") is not None and a.get("y") is not None:
        return _num(a["x"], "x"), _num(a["y"], "y")
    if required:
        raise ActionError(f"좌표가 필요합니다 ({' / '.join(names)})")
    return None


def normalize_action(a: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if not isinstance(a, dict):
        raise ActionError(f"액션은 JSON 객체여야 합니다: {a!r}")
    raw = a.get("action") or a.get("type") or a.get("name")
    if not raw or not isinstance(raw, str):
        raise ActionError(f"'action' 필드가 없습니다: {a!r}")
    name = raw.strip().lower()
    name = ALIASES.get(name, name)
    if name not in ACTIONS:
        raise ActionError(f"알 수 없는 액션입니다: {raw!r}")
    return name, a


class ActionRunner:
    """액션 목록을 순서대로 실행합니다. 동시에 들어온 요청은 배치 단위로 직렬화됩니다."""

    def __init__(self, backend: Backend, read_only: bool = False) -> None:
        self.backend = backend
        self.read_only = read_only
        self._lock = asyncio.Lock()
        self.last_input_at: float = 0.0

    async def run(self, actions: Sequence[dict[str, Any]], space: Any = None,
                  stop_on_error: bool = True) -> list[dict[str, Any]]:
        sp = parse_space(space)
        results: list[dict[str, Any]] = []
        async with self._lock:
            failed = False
            for i, a in enumerate(actions):
                if failed:
                    results.append({"index": i, "ok": False, "skipped": True,
                                    "error": "앞선 액션이 실패해 실행하지 않았습니다"})
                    continue
                t0 = time.perf_counter()
                try:
                    name, body = normalize_action(a)
                    local = parse_space(body["space"]) if "space" in body else (
                        Space(norm=True) if str(body.get("units", "")).lower() in ("norm", "normalized") else sp)
                    value = await self._run_one(name, body, local)
                    r: dict[str, Any] = {"index": i, "action": name, "ok": True}
                    if value is not None:
                        r["value"] = value
                    r["ms"] = round((time.perf_counter() - t0) * 1000, 1)
                    results.append(r)
                except (ActionError, KeyError_, BackendError) as e:
                    results.append({"index": i, "action": a.get("action") if isinstance(a, dict) else None,
                                    "ok": False, "error": str(e)})
                    failed = stop_on_error
                except Exception as e:  # 백엔드 내부 예외도 요청 단위로 보고
                    log.exception("action %d failed", i)
                    results.append({"index": i, "action": a.get("action") if isinstance(a, dict) else None,
                                    "ok": False, "error": f"{type(e).__name__}: {e}"})
                    failed = stop_on_error
            if any(not r["ok"] for r in results):
                await self.backend.release_all()
            self.last_input_at = time.time()
        return results

    def _xy(self, a: dict[str, Any], sp: Space, *names: str, required: bool = False) -> tuple[float, float] | None:
        p = _point(a, *names, required=required)
        if p is None:
            return None
        if self.backend.size == (0, 0) and (sp.norm or sp.width):
            raise ActionError("화면 크기를 아직 모릅니다. 먼저 스크린샷을 한 번 받아 주세요")
        return sp.to_frame(p[0], p[1], self.backend.size)

    async def _run_one(self, name: str, a: dict[str, Any], sp: Space) -> Any:
        b = self.backend
        if self.read_only and name not in ("wait", "screenshot", "cursor_position"):
            raise ActionError("읽기 전용(view-only) 모드라 입력이 차단되었습니다")

        if name == "screenshot":
            return None
        if name == "wait":
            secs = _num(a["ms"], "ms") / 1000 if "ms" in a else _num(a.get("duration", a.get("seconds", 1)), "duration")
            await asyncio.sleep(min(max(secs, 0.0), MAX_WAIT_SECONDS))
            return None
        if name == "cursor_position":
            x, y = sp.from_frame(*b.cursor, b.size)
            return [round(x, 2) if sp.norm else int(round(x)), round(y, 2) if sp.norm else int(round(y))]
        if name == "move":
            p = self._xy(a, sp, "coordinate", "position", "to", "x", required=True)
            await b.move(*p)
            return None
        if name in ("click", "double_click", "triple_click", "right_click", "middle_click"):
            p = self._xy(a, sp, "coordinate", "position", "x")
            button = {"right_click": "right", "middle_click": "middle"}.get(name, a.get("button", "left"))
            count = {"double_click": 2, "triple_click": 3}.get(name, int(_num(a.get("count", a.get("clicks", 1)), "count")))
            mods = _modifiers(a)
            await b.click(*(p if p else (None, None)), button=button, count=count, modifiers=mods)
            return None
        if name in ("mouse_down", "mouse_up"):
            p = self._xy(a, sp, "coordinate", "position", "x")
            if p:
                await b.move(*p)
            button = a.get("button", "left")
            count = int(_num(a.get("count", 1), "count"))
            await (b.mouse_down(button, count) if name == "mouse_down" else b.mouse_up(button, count))
            return None
        if name == "drag":
            if a.get("path"):
                path = a["path"]
                if not isinstance(path, list) or len(path) < 2:
                    raise ActionError("path 는 두 점 이상의 [[x,y], ...] 배열이어야 합니다")
                pts = [self._xy({"p": p}, sp, "p", required=True) for p in path]
            else:
                start = self._xy(a, sp, "from", "start", "start_coordinate")
                end = self._xy(a, sp, "to", "end", "coordinate", required=True)
                pts = [start or b.cursor, end]
            await b.drag(pts, button=a.get("button", "left"), steps=int(_num(a.get("steps", 10), "steps")),
                         modifiers=_modifiers(a))
            return None
        if name == "scroll":
            p = self._xy(a, sp, "coordinate", "position", "x")
            dx, dy = _scroll_amount(a)
            await b.scroll(dx, dy, *(p if p else (None, None)), modifiers=_modifiers(a))
            return None
        if name == "key":
            text = a.get("keys", a.get("key", a.get("combo", a.get("text"))))
            if not isinstance(text, str) or text == "":
                raise ActionError("key 액션에는 keys 문자열이 필요합니다 (예: \"ctrl+c\")")
            repeat = int(_num(a.get("repeat", 1), "repeat"))
            if not 1 <= repeat <= 100:
                raise ActionError("repeat 는 1~100 이어야 합니다")
            for combo in parse_key_sequence(text):
                await b.press(combo, repeat=repeat)
            return None
        if name in ("key_down", "key_up"):
            key = a.get("key", a.get("keys", a.get("text")))
            if not isinstance(key, str):
                raise ActionError(f"{name} 에는 key 가 필요합니다")
            k = normalize_key(key)
            await (b.key_down(k) if name == "key_down" else b.key_up(k))
            return None
        if name == "hold_key":
            text = a.get("keys", a.get("key", a.get("text")))
            if not isinstance(text, str):
                raise ActionError("hold_key 에는 keys 가 필요합니다")
            secs = _num(a["ms"], "ms") / 1000 if "ms" in a else _num(a.get("duration", 1), "duration")
            await b.hold_keys(parse_combo(text), min(max(secs, 0.0), MAX_WAIT_SECONDS))
            return None
        if name == "type":
            text = a.get("text", a.get("value"))
            if not isinstance(text, str):
                raise ActionError("type 액션에는 text 문자열이 필요합니다")
            await b.type_text(text, interval=_num(a.get("interval_ms", 0), "interval_ms") / 1000)
            return None
        if name == "navigate":
            url = a.get("url")
            if not isinstance(url, str) or not url:
                raise ActionError("navigate 에는 url 이 필요합니다")
            await b.navigate(url)
            return None
        raise ActionError(f"처리되지 않은 액션: {name}")  # pragma: no cover


def _modifiers(a: dict[str, Any]) -> tuple[str, ...]:
    # Claude 컴퓨터 사용 도구는 클릭·드래그·스크롤의 수정자를 text 필드로 보냅니다.
    m = a.get("modifiers", a.get("text"))
    if not m:
        return ()
    if isinstance(m, (list, tuple)):
        m = "+".join(m)
    return split_modifier_string(str(m))


def _scroll_amount(a: dict[str, Any]) -> tuple[int, int]:
    if "direction" in a or "scroll_direction" in a:
        d = str(a.get("direction", a.get("scroll_direction"))).lower()
        n = int(_num(a.get("amount", a.get("scroll_amount", 3)), "amount"))
        return {"up": (0, -n), "down": (0, n), "left": (-n, 0), "right": (n, 0)}.get(d) or _bad_dir(d)
    dx = int(_num(a.get("dx", 0), "dx"))
    dy = int(_num(a.get("dy", 0), "dy"))
    if dx == 0 and dy == 0:
        raise ActionError("scroll 에는 dx/dy 또는 direction+amount 가 필요합니다")
    return dx, dy


def _bad_dir(d: str) -> tuple[int, int]:
    raise ActionError(f"scroll direction 은 up/down/left/right 중 하나여야 합니다: {d!r}")


__all__ = ["ActionRunner", "ActionError", "Space", "parse_space", "normalize_action", "Combo"]
