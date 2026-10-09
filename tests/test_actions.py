import asyncio

import pytest

from vmonitor.actions import ActionRunner, Space, parse_space
from vmonitor.backends.demo import DemoBackend


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def demo():
    b = DemoBackend(width=960, height=600, input_delay=0)
    run(b.capture())
    return b


def test_click_buttons_and_type(demo):
    r = ActionRunner(demo)
    res = run(r.run([
        {"action": "click", "x": 100, "y": 120},
        {"action": "double_click", "x": 480, "y": 120},
        {"action": "type", "text": "안녕 hi"},
        {"action": "key", "keys": "backspace"},
    ]))
    assert all(x["ok"] for x in res), res
    assert demo.info()["buttons"] == {"A": 1, "B": 2, "C": 0}
    assert demo.state.text == "안녕 h"


def test_claude_computer_tool_fields(demo):
    """Claude 컴퓨터 사용 도구의 이름·필드를 그대로 받아야 합니다."""
    r = ActionRunner(demo)
    res = run(r.run([
        {"action": "left_click", "coordinate": [100, 120], "text": "shift"},
        {"action": "left_click_drag", "start_coordinate": [10, 300], "coordinate": [200, 400]},
        {"action": "scroll", "coordinate": [50, 50], "scroll_direction": "down", "scroll_amount": 2},
        {"action": "mouse_move", "coordinate": [30, 40]},
        {"action": "cursor_position"},
        {"action": "key", "text": "Return", "repeat": 2},
        {"action": "hold_key", "text": "shift", "duration": 0.01},
        {"action": "left_mouse_down"}, {"action": "left_mouse_up"},
        {"action": "wait", "duration": 0.01},
    ]))
    assert all(x["ok"] for x in res), res
    assert res[4]["value"] == [30, 40]
    assert demo.state.scroll == (0, 2)
    assert demo.state.last_drag == ((10, 300), (200, 400))
    assert any("[shift]" in e for e in demo.state.events)


def test_space_scaling_and_norm(demo):
    r = ActionRunner(demo)
    run(r.run([{"action": "move", "x": 240, "y": 150}], space=[480, 300]))
    assert demo.cursor == (480, 300)
    run(r.run([{"action": "move", "x": 0.25, "y": 0.5, "units": "norm"}]))
    assert demo.cursor == (240, 300)
    res = run(r.run([{"action": "cursor_position"}], space="norm"))
    assert res[0]["value"] == [0.25, 0.5]
    # 화면 밖 좌표는 가장자리로 고정
    run(r.run([{"action": "move", "x": 5000, "y": -10}]))
    assert demo.cursor == (959, 0)


def test_stop_on_error_and_release(demo):
    r = ActionRunner(demo)
    res = run(r.run([{"action": "key_down", "key": "ctrl"}, {"action": "bogus"}, {"action": "click", "x": 1, "y": 1}]))
    assert [x["ok"] for x in res] == [True, False, False]
    assert res[2].get("skipped")
    assert demo._keys_down == []  # 실패 시 눌린 키를 떼어 둠


def test_read_only(demo):
    r = ActionRunner(demo, read_only=True)
    res = run(r.run([{"action": "click", "x": 1, "y": 1}]))
    assert not res[0]["ok"] and "읽기 전용" in res[0]["error"]


def test_parse_space():
    assert parse_space([100, 50]) == Space(100.0, 50.0)
    assert parse_space("norm").norm
    with pytest.raises(ValueError):
        parse_space([0, 1])
