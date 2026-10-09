"""AI 연동 테스트: 파이썬 클라이언트, MCP 도구 서버, Claude 컴퓨터 사용 에이전트 루프.

에이전트 테스트는 실제 API 를 부르지 않고, Anthropic SDK 의 HTTP 계층을 가짜 전송(MockTransport)으로
바꿔 SDK 가 만든 요청 본문을 검사하고 준비된 응답을 돌려줍니다.
"""

import asyncio
import json

import pytest

from vmonitor.client import MonitorClient, MonitorError


def test_client_roundtrip(live_server):
    m = MonitorClient(live_server.url)
    assert m.size() == (960, 600)
    data, meta = m.screenshot(max_width=480)
    assert meta["width"] == 480 and meta["frame_width"] == 960 and data[:4] == b"\x89PNG"
    m.click(100, 120)
    m.type("hey")
    m.key("backspace")
    assert live_server.backend.state.text == "he"
    assert live_server.backend.info()["buttons"]["A"] == 1
    assert m.wait_for_color(5, 5, m.pixel(5, 5), timeout=2)
    with pytest.raises(MonitorError):
        m.act("nope")


def test_mcp_tools(live_server):
    pytest.importorskip("mcp")
    from vmonitor.mcp_server import build_mcp

    mcp = build_mcp(live_server.url, max_width=480, max_height=300)

    async def main():
        tools = {t.name for t in await mcp.list_tools()}
        assert {"screenshot", "click", "type_text", "press_key", "scroll", "drag", "run_actions"} <= tools
        shot = await mcp.call_tool("screenshot", {})
        blocks = shot.content if hasattr(shot, "content") else shot
        assert any(getattr(b, "type", None) == "image" for b in blocks)
        # 스크린샷(480x300) 기준 좌표 (50,60) → 실제 화면 (100,120) = 버튼 A
        await mcp.call_tool("click", {"x": 50, "y": 60})
        await mcp.call_tool("type_text", {"text": "mcp"})
        await mcp.call_tool("run_actions", {"actions": [{"action": "key", "keys": "backspace"}]})

    asyncio.run(main())
    assert live_server.backend.info()["buttons"]["A"] == 1
    assert live_server.backend.state.text == "mc"


def _msg(content, stop_reason):
    return {"id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": content, "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 10}}


def _tool(i, name, inp):
    return {"type": "tool_use", "id": f"toolu_{i}", "name": name, "input": inp, "toolset_name": "computer"}


def test_claude_agent_loop(live_server):
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    from vmonitor.agents.claude_agent import run_agent

    sent = []
    # 데모 화면 960x600 → 에이전트가 보는 스크린샷은 max 480x300 (scale 0.5)
    replies = [
        _msg([{"type": "text", "text": "버튼 A 를 누르고 입력하겠습니다."},
              _tool(1, "left_click", {"coordinate": [50, 60]}),
              _tool(2, "type", {"text": "claude"}),
              _tool(3, "key", {"text": "BackSpace", "repeat": 2})], "tool_use"),
        _msg([_tool(4, "zoom", {"region": [0, 0, 240, 150]}),
              _tool(5, "cursor_position", {}),
              _tool(6, "bogus_member", {}),
              _tool(7, "screenshot", {})], "tool_use"),
        _msg([{"type": "text", "text": "완료했습니다."}], "end_turn"),
    ]

    def handler(request):
        body = json.loads(request.content)
        sent.append((dict(request.headers), body))
        return httpx2.Response(200, json=replies[len(sent) - 1])

    api = anthropic.Anthropic(api_key="test-key", base_url="http://anthropic.test",
                              http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    logs = []
    ok = run_agent("버튼 A 를 누르고 claude 라고 입력해", server=live_server.url, max_width=480, max_height=300,
                   client=api, out=logs.append)
    assert ok, logs

    # 요청 형태 검증
    headers, first = sent[0]
    assert first["model"] == "claude-opus-5-5"
    assert first["tools"] == [{"type": "computer_toolset_20260801"}]
    assert first["thinking"] == {"type": "adaptive", "display": "updates"}
    assert first["output_config"] == {"effort": "medium"}
    assert first["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in headers["anthropic-beta"]
    assert first["messages"][0]["content"][-1]["type"] == "image"

    # 1차 배치 결과: 모든 결과가 toolset_name 을 되돌리고, 마지막 결과에 화면이 붙음
    _, second = sent[1]
    results = second["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["toolu_1", "toolu_2", "toolu_3"]
    assert all(r["toolset_name"] == "computer" and not r.get("is_error") for r in results)
    assert results[-1]["content"][-1]["type"] == "image"

    # 2차 배치: zoom 은 이미지, cursor_position 은 스크린샷 좌표, 알 수 없는 도구에서 멈추고 나머지는 미실행
    _, third = sent[2]
    r2 = third["messages"][-1]["content"]
    assert r2[0]["content"][0]["type"] == "image"
    assert r2[1]["content"][0]["text"] == "X=50,Y=60"
    assert r2[2]["is_error"] and r2[3]["is_error"] and r2[3]["content"].startswith("Not executed")

    st = live_server.backend
    assert st.info()["buttons"]["A"] == 1
    assert st.state.text == "clau"
    assert any("완료" in line for line in logs)


def test_claude_agent_refusal(live_server):
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    from vmonitor.agents.claude_agent import run_agent

    def handler(request):
        m = _msg([], "refusal")
        m["stop_details"] = {"type": "refusal", "category": "cyber", "explanation": "test"}
        return httpx2.Response(200, json=m)

    api = anthropic.Anthropic(api_key="k", base_url="http://anthropic.test",
                              http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    logs = []
    assert run_agent("x", server=live_server.url, client=api, out=logs.append, fallback=False) is False
    assert any("거절" in line for line in logs)
