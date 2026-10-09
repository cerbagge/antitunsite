"""MCP 도구 서버 - Claude Desktop, Claude Code, Cursor 등 MCP 를 지원하는 AI 가 VMonitor 화면을 보고 조작하게 합니다.

실행 중인 VMonitor 서버(``vmonitor serve``)에 붙는 얇은 클라이언트이며, stdio 로 통신합니다.
AI 에게는 축소된 스크린샷(기본 최대 1280x800)을 보여 주고, AI 가 그 이미지 기준으로 준 좌표를
실제 화면 좌표로 자동 환산합니다.

Claude Desktop 설정 예 (claude_desktop_config.json):
    {"mcpServers": {"vmonitor": {"command": "vmonitor", "args": ["mcp", "--server", "http://127.0.0.1:8765"]}}}
Claude Code:
    claude mcp add vmonitor -- vmonitor mcp --server http://127.0.0.1:8765
"""

from __future__ import annotations

from typing import Any

from .client import MonitorClient
from .hub import fit_scale

INSTRUCTIONS = (
    "VMonitor 로 송출되는 앱 창/브라우저 화면 하나를 조작합니다. 먼저 screenshot 으로 화면을 보고, "
    "좌표는 가장 최근 screenshot 이미지의 픽셀 좌표(왼쪽 위 0,0)로 지정하세요. "
    "화면이 바뀌는 동작 뒤에는 screenshot 으로 결과를 확인하세요."
)


# 도구의 반환 타입(Image)을 MCP 가 주석에서 읽어 이미지 콘텐츠로 바꾸므로, 모듈 수준에서 가져와야 합니다.
try:  # mcp 2.x
    from mcp.server.mcpserver import Image
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    try:
        from mcp.server.fastmcp import FastMCP as _Server
        from mcp.server.fastmcp import Image
    except ImportError:  # mcp 미설치: build_mcp 에서 안내
        _Server = Image = None  # type: ignore[assignment,misc]


class _View:
    """AI 가 보는 스크린샷 크기(좌표계)를 기억합니다."""

    def __init__(self, client: MonitorClient, max_width: int, max_height: int) -> None:
        self.client = client
        self.max_width = max_width
        self.max_height = max_height
        self.size: tuple[int, int] | None = None

    def space(self) -> list[int]:
        if self.size is None:
            fw, fh = self.client.size()
            s = fit_scale(fw, fh, self.max_width, self.max_height)
            self.size = (max(1, round(fw * s)), max(1, round(fh * s)))
        return list(self.size)

    def shot(self, grid: int | None = None) -> bytes:
        data, meta = self.client.screenshot(fmt="png", max_width=self.max_width, max_height=self.max_height, grid=grid)
        self.size = (meta["width"], meta["height"])
        return data


def build_mcp(server: str, token: str | None = None, max_width: int = 1280, max_height: int = 800) -> Any:
    if _Server is None:
        raise RuntimeError("mcp 패키지가 없습니다: pip install mcp")
    client = MonitorClient(server, token=token)
    view = _View(client, max_width, max_height)
    mcp = _Server("vmonitor", instructions=INSTRUCTIONS)

    def run(*actions: dict[str, Any]) -> str:
        r = client.actions(list(actions), space=view.space(), check=False)
        bad = [x for x in r["results"] if not x.get("ok")]
        if bad:
            raise RuntimeError(bad[0].get("error", "실패"))
        vals = [x["value"] for x in r["results"] if "value" in x]
        return "OK" if not vals else str(vals[-1])

    @mcp.tool()
    def screenshot(grid: int | None = None) -> Image:
        """현재 화면을 이미지로 봅니다. 다른 도구의 좌표는 이 이미지의 픽셀 좌표입니다.
        grid 에 숫자(예: 100)를 주면 원본 화면 기준 그 간격으로 좌표 눈금을 그려 줍니다."""
        return Image(data=view.shot(grid), format="png")

    @mcp.tool()
    def screen_info() -> dict[str, Any]:
        """대상 정보: 백엔드 종류, 실제 화면 크기, AI 좌표계 크기, URL/창 제목 등."""
        i = client.info()
        return {"backend": i["backend"], "frame_size": i["size"], "view_size": view.space(),
                "details": i.get("backend_info", {}), "last_error": i.get("last_error")}

    @mcp.tool()
    def click(x: int, y: int, button: str = "left", count: int = 1, modifiers: str | None = None) -> str:
        """(x, y) 를 클릭합니다. button: left/right/middle, count: 2 면 더블클릭, modifiers 예: "ctrl", "shift+ctrl"."""
        return run({"action": "click", "x": x, "y": y, "button": button, "count": count, "modifiers": modifiers})

    @mcp.tool()
    def move_mouse(x: int, y: int) -> str:
        """마우스를 (x, y) 로 옮깁니다(호버)."""
        return run({"action": "move", "x": x, "y": y})

    @mcp.tool()
    def drag(start_x: int, start_y: int, end_x: int, end_y: int, button: str = "left") -> str:
        """(start_x, start_y) 에서 눌러 (end_x, end_y) 까지 끌어다 놓습니다."""
        return run({"action": "drag", "from": [start_x, start_y], "to": [end_x, end_y], "button": button})

    @mcp.tool()
    def scroll(x: int, y: int, direction: str = "down", amount: int = 3) -> str:
        """(x, y) 위치에서 휠을 굴립니다. direction: up/down/left/right, amount: 칸 수."""
        return run({"action": "scroll", "x": x, "y": y, "direction": direction, "amount": amount})

    @mcp.tool()
    def type_text(text: str) -> str:
        """현재 포커스된 곳에 문자열을 입력합니다(한글 가능)."""
        return run({"action": "type", "text": text})

    @mcp.tool()
    def press_key(keys: str, repeat: int = 1) -> str:
        """키나 단축키를 누릅니다. 예: "enter", "ctrl+c", "alt+tab", "ctrl+a backspace"(공백으로 여러 개)."""
        return run({"action": "key", "keys": keys, "repeat": repeat})

    @mcp.tool()
    def navigate(url: str) -> str:
        """(브라우저 백엔드 전용) 현재 탭에서 url 을 엽니다."""
        return run({"action": "navigate", "url": url})

    @mcp.tool()
    def wait(seconds: float = 1.0) -> str:
        """화면이 바뀌길 기다립니다(최대 60초)."""
        return run({"action": "wait", "duration": min(max(seconds, 0.0), 60.0)})

    @mcp.tool()
    def run_actions(actions: list[dict[str, Any]]) -> str:
        """여러 액션을 한 번에 순서대로 실행합니다. 좌표는 스크린샷 이미지 기준.
        예: [{"action":"click","x":10,"y":20},{"action":"type","text":"hi"},{"action":"key","keys":"enter"}]"""
        return run(*actions)

    return mcp


def run_mcp(server: str, token: str | None = None, max_width: int = 1280, max_height: int = 800) -> None:
    build_mcp(server, token, max_width, max_height).run("stdio")
