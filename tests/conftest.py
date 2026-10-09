from __future__ import annotations

import os
import socket
import threading
import time

import pytest
import uvicorn

from vmonitor.backends.demo import DemoBackend
from vmonitor.server import create_app


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveServer:
    def __init__(self, backend: DemoBackend, token: str | None = None) -> None:
        self.backend = backend
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        app = create_app(backend, fps=10, token=token)
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> "LiveServer":
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("server did not start")
            time.sleep(0.05)
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.should_exit = True
        self.thread.join(10)


@pytest.fixture
def live_server():
    """데모 백엔드로 실제 HTTP 서버를 띄웁니다 (클라이언트·MCP·에이전트 테스트용)."""
    backend = DemoBackend(width=960, height=600, input_delay=0)
    with LiveServer(backend) as srv:
        yield srv


STRICT = os.environ.get("VMONITOR_STRICT_TESTS") == "1"


def require(reason: str) -> None:
    """실행 환경이 없어 통합 테스트를 할 수 없을 때: 평소엔 건너뛰고, CI(엄격 모드)에선 실패시킵니다."""
    if STRICT:
        pytest.fail(f"[VMONITOR_STRICT_TESTS] {reason}")
    pytest.skip(reason)


@pytest.fixture
def unavailable():
    return require
