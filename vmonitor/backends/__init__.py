"""백엔드 생성. 각 백엔드는 필요할 때만 import 해서 선택 의존성(Playwright 등)이 없어도 나머지는 동작합니다."""

from __future__ import annotations

from typing import Any

from .base import Backend, BackendError, NotSupportedError

KINDS = ("demo", "browser", "window", "x11")


def create_backend(kind: str, **opts: Any) -> Backend:
    kind = kind.lower()
    if kind == "demo":
        from .demo import DemoBackend

        return DemoBackend(**opts)
    if kind == "browser":
        from .browser import BrowserBackend

        return BrowserBackend(**opts)
    if kind in ("window", "windows", "win32"):
        from .windows import WindowsBackend

        return WindowsBackend(**opts)
    if kind in ("x11", "linux", "xvfb"):
        from .x11 import X11Backend

        return X11Backend(**opts)
    raise ValueError(f"알 수 없는 백엔드: {kind!r} (사용 가능: {', '.join(KINDS)})")


__all__ = ["Backend", "BackendError", "NotSupportedError", "create_backend", "KINDS"]
