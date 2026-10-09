"""HTTP / WebSocket 게이트웨이.

엔드포인트 요약 (자세한 규격은 README 와 /docs 의 자동 문서 참고)

  GET  /                      사람이 보는 웹 뷰어(실시간 화면 + 마우스·키보드 조작 + 좌표 확인)
  GET  /stream.mjpg           MJPEG 실시간 영상 (브라우저 <img>, OpenCV, VLC, OBS 등에서 바로 재생)
  GET  /api/info              대상 정보(화면 크기, 커서, fps, 백엔드 상태)
  GET  /api/screenshot        지금 화면 한 장 (PNG/JPEG, 축소·격자·커서 표시 옵션)
  GET  /api/pixel?x=&y=       한 점의 색 (매크로의 '색 바뀔 때까지 대기'용)
  POST /api/actions           입력 액션 실행 (+ 실행 후 스크린샷 동봉 옵션)
  WS   /ws                    양방향: JSON 액션 ↔ 결과, 선택적으로 JPEG 프레임 수신
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import secrets
import time
from importlib import resources
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from . import __version__
from .actions import ActionRunner
from .backends.base import Backend
from .hub import Encoded, FrameHub

log = logging.getLogger(__name__)


class ScreenshotOptions(BaseModel):
    format: str = "png"
    quality: int = 85
    max_width: int | None = None
    max_height: int | None = None
    grid: int | None = None
    cursor: bool = False


class ActionsRequest(BaseModel):
    actions: list[dict[str, Any]] | dict[str, Any] = Field(..., description="액션 객체 또는 그 배열")
    space: Any = Field(None, description='좌표계: [너비, 높이] 또는 "norm". 생략하면 실제 화면 픽셀')
    screenshot: bool | ScreenshotOptions = Field(False, description="실행 후 스크린샷을 base64 로 함께 받기")
    settle_ms: int = Field(300, description="스크린샷 전에 화면이 바뀌길 기다리는 시간(ms)")
    stop_on_error: bool = True


def _encoded_json(enc: Encoded, frame_size: tuple[int, int], seq: int) -> dict[str, Any]:
    return {"mime": enc.mime, "data": base64.b64encode(enc.data).decode(), "width": enc.width,
            "height": enc.height, "scale": enc.scale, "frame_width": frame_size[0],
            "frame_height": frame_size[1], "seq": seq}


def create_app(backend: Backend, fps: float = 10.0, token: str | None = None, read_only: bool = False,
               title: str | None = None) -> FastAPI:
    hub = FrameHub(backend, fps=fps)
    runner = ActionRunner(backend, read_only=read_only)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        await backend.start()
        await hub.start()
        try:
            await hub.fresh()
        except Exception as e:  # 첫 캡처 실패는 치명적이지 않음(창이 늦게 뜨는 경우 등)
            log.warning("첫 화면 캡처 실패: %s", e)
        w, h = backend.size
        log.info("송출 시작: %s 백엔드, %dx%d, %.0f fps", backend.name, w, h, hub.fps)
        yield
        await hub.stop()
        with contextlib.suppress(Exception):
            await backend.release_all()
        await backend.stop()

    app = FastAPI(title="VMonitor", version=__version__, lifespan=lifespan,
                  description="앱/브라우저 화면을 모니터처럼 송출하고 AI·매크로가 입력을 보내는 게이트웨이")
    app.state.hub = hub
    app.state.runner = runner
    app.state.backend = backend

    def _check_token(supplied: str | None) -> bool:
        return token is None or (supplied is not None and secrets.compare_digest(supplied, token))

    def _token_from(headers: Any, query: Any) -> str | None:
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return headers.get("x-api-key") or query.get("token")

    async def require_auth(request: Request) -> None:
        if not _check_token(_token_from(request.headers, request.query_params)):
            raise HTTPException(401, "토큰이 필요합니다 (Authorization: Bearer <token> 또는 ?token=)")

    def info() -> dict[str, Any]:
        f = hub.frame
        return {
            "name": "vmonitor", "version": __version__, "title": title, "backend": backend.name,
            "size": list(backend.size), "cursor": list(backend.cursor), "fps": hub.fps,
            "watchers": hub.watchers, "read_only": read_only, "frame_seq": f.seq if f else 0,
            "frame_age_s": round(time.time() - f.ts, 3) if f else None, "capture_ms": round(hub.capture_ms, 1),
            "last_error": hub.last_error, "auth": token is not None, "backend_info": backend.info(),
        }

    # ------------------------------------------------------------------ 뷰어
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def viewer() -> HTMLResponse:
        html = resources.files("vmonitor.web").joinpath("viewer.html").read_text(encoding="utf-8")
        return HTMLResponse(html.replace("{{TITLE}}", title or "VMonitor"))

    @app.get("/api/info", dependencies=[Depends(require_auth)])
    async def api_info() -> dict[str, Any]:
        return info()

    # ------------------------------------------------------------------ 화면
    @app.get("/api/screenshot", dependencies=[Depends(require_auth)],
             responses={200: {"content": {"image/png": {}, "image/jpeg": {}}}})
    async def api_screenshot(
        format: str = Query("png", pattern="^(png|jpe?g)$"),
        quality: int = Query(85, ge=1, le=95),
        max_width: int | None = Query(None, ge=16),
        max_height: int | None = Query(None, ge=16),
        grid: int | None = Query(None, ge=10, description="원본 좌표 기준 N 픽셀마다 좌표 눈금"),
        cursor: bool = False,
        fresh: bool = Query(True, description="false 면 가장 최근 프레임을 재사용(빠름)"),
        as_json: bool = Query(False, alias="json", description="true 면 base64 JSON 으로 응답"),
    ) -> Response:
        try:
            f = await (hub.fresh() if fresh else hub.latest())
        except Exception as e:
            raise HTTPException(503, f"화면 캡처 실패: {e}") from e
        enc = await asyncio.to_thread(f.encode, format, quality, max_width, max_height, grid,
                                      backend.cursor if cursor else None)
        if as_json:
            return JSONResponse(_encoded_json(enc, f.size, f.seq))
        headers = {"X-Frame-Width": str(f.size[0]), "X-Frame-Height": str(f.size[1]),
                   "X-Image-Width": str(enc.width), "X-Image-Height": str(enc.height),
                   "X-Scale": f"{enc.scale:.6f}", "X-Frame-Seq": str(f.seq), "Cache-Control": "no-store"}
        return Response(enc.data, media_type=enc.mime, headers=headers)

    @app.get("/api/pixel", dependencies=[Depends(require_auth)])
    async def api_pixel(x: int, y: int, fresh: bool = False) -> dict[str, Any]:
        f = await (hub.fresh() if fresh else hub.latest(max_age=0.5))
        w, h = f.size
        if not (0 <= x < w and 0 <= y < h):
            raise HTTPException(400, f"좌표가 화면({w}x{h}) 밖입니다")
        r, g, b = f.image.getpixel((x, y))[:3]
        return {"x": x, "y": y, "rgb": [r, g, b], "hex": f"#{r:02x}{g:02x}{b:02x}", "seq": f.seq}

    @app.get("/stream.mjpg", dependencies=[Depends(require_auth)])
    async def stream(fps: float | None = Query(None, gt=0, le=60), quality: int = Query(70, ge=1, le=95),
                     max_width: int | None = Query(None, ge=16), max_height: int | None = Query(None, ge=16),
                     grid: int | None = Query(None, ge=10)) -> StreamingResponse:
        async def gen():  # type: ignore[no-untyped-def]
            frames = hub.frames(max_fps=fps)
            try:
                async for f in frames:
                    enc = await asyncio.to_thread(f.encode, "jpeg", quality, max_width, max_height, grid, None)
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                           + str(len(enc.data)).encode() + b"\r\n\r\n" + enc.data + b"\r\n")
            finally:
                await frames.aclose()

        return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame",
                                 headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------------ 입력
    async def run_actions(req: ActionsRequest, who: str) -> dict[str, Any]:
        actions = req.actions if isinstance(req.actions, list) else [req.actions]
        results = await runner.run(actions, space=req.space, stop_on_error=req.stop_on_error)
        ok = all(r["ok"] for r in results)
        names = ",".join(str(r.get("action")) for r in results)
        log.info("입력 %s ← %s: %s%s", "OK" if ok else "실패", who, names,
                 "" if ok else f" ({next(r['error'] for r in results if not r['ok'])})")
        out: dict[str, Any] = {"ok": ok, "results": results, "size": list(backend.size),
                               "cursor": list(backend.cursor)}
        if req.screenshot:
            opts = req.screenshot if isinstance(req.screenshot, ScreenshotOptions) else ScreenshotOptions()
            await asyncio.sleep(max(0, req.settle_ms) / 1000)
            f = await hub.fresh()
            enc = await asyncio.to_thread(f.encode, opts.format, opts.quality, opts.max_width, opts.max_height,
                                          opts.grid, backend.cursor if opts.cursor else None)
            out["screenshot"] = _encoded_json(enc, f.size, f.seq)
        return out

    @app.post("/api/actions", dependencies=[Depends(require_auth)])
    async def api_actions(req: ActionsRequest, request: Request) -> dict[str, Any]:
        return await run_actions(req, request.client.host if request.client else "?")

    # ------------------------------------------------------------------ WebSocket
    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket, frames: bool = False, fps: float | None = None, quality: int = 70,
                          max_width: int | None = None, max_height: int | None = None) -> None:
        if not _check_token(_token_from(ws.headers, ws.query_params)):
            await ws.close(code=4401, reason="unauthorized")
            return
        await ws.accept()
        who = f"ws:{ws.client.host if ws.client else '?'}"
        send_lock = asyncio.Lock()

        async def sender() -> None:
            agen = hub.frames(max_fps=fps)
            try:
                async for f in agen:
                    enc = await asyncio.to_thread(f.encode, "jpeg", quality, max_width, max_height, None, None)
                    async with send_lock:
                        await ws.send_bytes(enc.data)
            finally:
                await agen.aclose()

        async def receiver() -> None:
            while True:
                msg = await ws.receive_json()
                mid = msg.get("id") if isinstance(msg, dict) else None
                try:
                    if isinstance(msg, dict) and msg.get("cmd") == "info":
                        reply: dict[str, Any] = {"id": mid, "ok": True, "info": info()}
                    else:
                        if isinstance(msg, dict) and "actions" in msg:
                            req = ActionsRequest(**msg)
                        else:
                            req = ActionsRequest(actions=msg)
                        reply = {"id": mid, **(await run_actions(req, who))}
                except Exception as e:
                    reply = {"id": mid, "ok": False, "error": f"{type(e).__name__}: {e}"}
                async with send_lock:
                    await ws.send_json(reply)

        tasks = [asyncio.create_task(receiver())]
        if frames:
            tasks.append(asyncio.create_task(sender()))
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            for t in done:
                exc = t.exception()
                if exc and not isinstance(exc, WebSocketDisconnect):
                    log.debug("ws 종료: %r", exc)
        finally:
            for t in tasks:
                t.cancel()
            with contextlib.suppress(Exception):
                await backend.release_all()  # 연결이 끊기면 눌린 키·버튼이 남지 않게

    return app
