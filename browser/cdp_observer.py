"""
Read-only Chrome DevTools Protocol (CDP) observer over WebSocket.

Connects to the browser's remote-debugging endpoint (see session_manager
--remote-debugging-port). Does not drive the page — Playwright remains the sole actor.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

try:
    import websockets
except ImportError:  # pragma: no cover
    websockets = None  # type: ignore


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


class CdpReadOnlyObserver:
    """
    Subscribes to CDP Network / Page / Runtime events via WebSocket (read-only).
    Writes raw.ndjson plus filtered network.jsonl, console.jsonl, page.jsonl.
    """

    def __init__(
        self,
        run_id: str,
        *,
        host: str = "127.0.0.1",
        port: Optional[int] = None,
        base_dir: str = ".o11y",
        page_url_hint: Optional[str] = None,
    ) -> None:
        self.run_id = run_id
        self.host = host
        self.port = int(port or os.getenv("CDP_DEBUG_PORT", "9222"))
        self._page_url_hint = (page_url_hint or "").lower().strip() or None
        self.root = os.path.join(base_dir, run_id)
        self._raw_path = os.path.join(self.root, "raw.ndjson")
        self._net_path = os.path.join(self.root, "network.jsonl")
        self._console_path = os.path.join(self.root, "console.jsonl")
        self._page_path = os.path.join(self.root, "page.jsonl")
        self._ws: Any = None
        self._reader_task: Optional[asyncio.Task[Any]] = None
        self._stop = asyncio.Event()
        self._next_id = 1

    def _mkdirs(self) -> None:
        os.makedirs(self.root, exist_ok=True)

    def _append(self, path: str, obj: dict[str, Any]) -> None:
        line = json.dumps(obj, default=str) + "\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)

    async def _resolve_page_websocket_url(self) -> str:
        """Pick a page target from /json/list (prefer page_url_hint), else /json/version."""
        base = f"http://{self.host}:{self.port}"
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{base}/json/list")
            r.raise_for_status()
            targets = r.json()
        if isinstance(targets, list):
            if self._page_url_hint:
                for t in targets:
                    if t.get("type") != "page":
                        continue
                    url = (t.get("url") or "").lower()
                    ws = t.get("webSocketDebuggerUrl")
                    if ws and self._page_url_hint in url:
                        return ws
            for t in targets:
                if t.get("type") != "page":
                    continue
                url = (t.get("url") or "").lower()
                ws = t.get("webSocketDebuggerUrl")
                if ws and "claude.ai" in url:
                    return ws
            for t in targets:
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                    return t["webSocketDebuggerUrl"]

        async with httpx.AsyncClient(timeout=5.0) as client:
            r2 = await client.get(f"{base}/json/version")
            r2.raise_for_status()
            ver = r2.json()
        ws = ver.get("webSocketDebuggerUrl")
        if not ws:
            raise RuntimeError("No webSocketDebuggerUrl from /json/version or /json/list")
        return ws

    async def _send_cmd(self, method: str, params: Optional[dict[str, Any]] = None) -> None:
        if self._ws is None:
            return
        cmd = {"id": self._next_id, "method": method, "params": params or {}}
        self._next_id += 1
        await self._ws.send(json.dumps(cmd))

    def _route_filtered(self, method: str, params: Any) -> None:
        evt = {"timestamp": _ts(), "event_type": method, "payload": params}
        if method.startswith("Network."):
            slim = dict(evt)
            if isinstance(params, dict):
                if method == "Network.requestWillBeSent":
                    req = params.get("request") or {}
                    slim["payload"] = {
                        "requestId": params.get("requestId"),
                        "url": req.get("url"),
                        "method": req.get("method"),
                    }
                elif method == "Network.responseReceived":
                    resp = params.get("response") or {}
                    slim["payload"] = {
                        "requestId": params.get("requestId"),
                        "url": resp.get("url"),
                        "status": resp.get("status"),
                        "mimeType": resp.get("mimeType"),
                    }
            self._append(self._net_path, slim)
        elif method == "Runtime.consoleAPICalled":
            self._append(self._console_path, evt)
        elif method.startswith("Page."):
            self._append(self._page_path, evt)

    async def _reader_loop(self) -> None:
        assert self._ws is not None
        try:
            while not self._stop.is_set():
                try:
                    raw = await asyncio.wait_for(self._ws.recv(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break
                except Exception:
                    break
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if "method" in msg:
                    method = msg["method"]
                    params = msg.get("params")
                    self._append(
                        self._raw_path,
                        {"timestamp": _ts(), "event_type": method, "payload": params},
                    )
                    if method in (
                        "Network.requestWillBeSent",
                        "Network.responseReceived",
                        "Runtime.consoleAPICalled",
                        "Page.frameNavigated",
                        "Page.loadEventFired",
                    ):
                        self._route_filtered(method, params)
        except Exception as e:
            logger.debug("CDP reader ended: %s", e)

    async def start(self) -> None:
        if websockets is None:
            logger.warning("websockets package not installed — CDP observer skipped")
            return
        try:
            self._mkdirs()
            ws_url = await self._resolve_page_websocket_url()
            logger.info("CDP observer connecting: %s", ws_url[:80])
            self._ws = await websockets.connect(ws_url, max_size=50 * 1024 * 1024)
            await self._send_cmd("Network.enable")
            await self._send_cmd("Page.enable")
            await self._send_cmd("Runtime.enable")
            self._stop.clear()
            self._reader_task = asyncio.create_task(self._reader_loop())
            logger.info("CDP observer started (read-only) → %s", self.root)
        except Exception as e:
            logger.warning("CDP observer start failed (ignored): %s", e)
            await self._cleanup_ws()

    async def _cleanup_ws(self) -> None:
        self._stop.set()
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def stop(self) -> None:
        await self._cleanup_ws()
        logger.info("CDP observer stopped (run_id=%s)", self.run_id)
