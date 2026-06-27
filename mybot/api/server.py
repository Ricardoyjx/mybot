"""Starlette Web 服务：静态文件 + WebSocket 流式对话。"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

from starlette.applications import Starlette
from starlette.routing import Route, WebSocketRoute, Mount
from starlette.responses import FileResponse, JSONResponse
from starlette.websockets import WebSocket, WebSocketDisconnect
from starlette.staticfiles import StaticFiles
import uvicorn

from loguru import logger

from mybot.agent.loop import AgentLoop


class WebServer:
    """异步 Web 服务，提供前端页面和 WebSocket 流式对话。"""

    def __init__(
        self,
        agent: AgentLoop,
        host: str = "0.0.0.0",
        port: int = 8080,
        webui_dir: Path | None = None,
    ):
        self.agent = agent
        self.host = host
        self.port = port
        self.webui_dir = (
            webui_dir or Path(__file__).resolve().parent.parent.parent / "webui"
        )
        self._server: uvicorn.Server | None = None

        self.app = Starlette(
            routes=[
                Route("/", self._handle_index),
                Route("/health", self._handle_health),
                WebSocketRoute("/ws/chat", self._handle_ws),
                Mount(
                    "/static", StaticFiles(directory=str(self.webui_dir)), name="static"
                ),
            ],
        )

    # --- HTTP Handlers ---

    async def _handle_index(self, request) -> FileResponse:
        index = self.webui_dir / "index.html"
        if not index.exists():
            return JSONResponse(
                {"error": "webui/index.html not found"}, status_code=404
            )
        return FileResponse(index)

    async def _handle_health(self, request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    # --- WebSocket Handler ---

    async def _handle_ws(self, ws: WebSocket) -> None:
        await ws.accept()
        # Use session ID from frontend query param, or generate a new one
        session_id = ws.query_params.get("session") or f"ws:{uuid.uuid4().hex[:8]}"
        logger.info("WS connected: {}", session_id)

        try:
            while True:
                raw = await ws.receive_text()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    data = {"type": "message", "content": raw}

                if data.get("type") == "message":
                    content = data.get("content", "")
                    if not content.strip():
                        continue
                    await self._process(ws, content, session_id)

        except WebSocketDisconnect:
            pass
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.exception("WS error for {}: {}", session_id, e)
        finally:
            logger.info("WS disconnected: {}", session_id)

    async def _process(
        self,
        ws: WebSocket,
        content: str,
        session_id: str,
    ) -> None:
        """处理用户消息，流式推送回复。"""
        stream_sent = False

        async def on_stream(delta: str) -> None:
            nonlocal stream_sent
            stream_sent = True
            try:
                await ws.send_json({"type": "delta", "content": delta})
            except Exception:
                pass

        async def on_stream_end() -> None:
            try:
                await ws.send_json({"type": "done"})
            except Exception:
                pass

        async def on_status(status: str) -> None:
            try:
                await ws.send_json({"type": "status", "content": status})
            except Exception:
                pass

        try:
            response = await self.agent.process_direct(
                content=content,
                session_key=session_id,
                channel="ws",
                chat_id=session_id,
                on_stream=on_stream,
                on_stream_end=on_stream_end,
                on_status=on_status,
            )
            # fallback: 流式没触发时发送完整响应
            if not stream_sent and response and response.content:
                await ws.send_json({"type": "delta", "content": response.content})
                await ws.send_json({"type": "done"})

        except Exception as e:
            logger.exception("WS handler error: {}", e)
            try:
                await ws.send_json({"type": "error", "content": str(e)})
            except Exception:
                pass

    # --- 启动/停止 ---

    async def start(self) -> None:
        """启动 web 服务（非阻塞，后台运行）。"""
        config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            log_level="warning",
        )
        self._server = uvicorn.Server(config)
        # 在后台 task 中运行，不阻塞调用方
        import asyncio

        self._task = asyncio.create_task(self._server.serve())
        logger.info("Web server started on http://{}:{}", self.host, self.port)

    async def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
