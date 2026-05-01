from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from playwright.async_api import Page

logger = logging.getLogger(__name__)


def tracing_enabled() -> bool:
    """When True, failed sends get one CDP + snapshot retry (see core.orchestrator).

    Also enables ``--remote-debugging-port`` on Chrome (see browser.session_manager).
    Successful requests never start tracers.
    """
    return os.getenv("ENABLE_TRACING", "false").lower() in ("1", "true", "yes")


class ObservabilityTracer:
    """Periodic screenshots + DOM snapshots under .o11y/{run_id}/.

    When ``snapshots_only=True`` (failure CDP runs), skips Playwright request/console
    listeners so CDP observer owns network.jsonl / console.jsonl.
    """

    def __init__(self, run_id: str, base_dir: str = ".o11y") -> None:
        self.run_id = run_id
        self.root = os.path.join(base_dir, run_id)
        self.screens_dir = os.path.join(self.root, "screenshots")
        self.dom_dir = os.path.join(self.root, "dom")
        self._net_path = os.path.join(self.root, "network.jsonl")
        self._console_path = os.path.join(self.root, "console.jsonl")
        self._summary_path = os.path.join(self.root, "summary.json")
        self._page: Optional[Page] = None
        self._shot_task: Optional[asyncio.Task[Any]] = None
        self._stop = asyncio.Event()
        self._shot_count = 0
        self._started_at = 0.0
        self._started = False
        self._snapshots_only = False

    def _mkdirs(self) -> None:
        os.makedirs(self.screens_dir, exist_ok=True)
        os.makedirs(self.dom_dir, exist_ok=True)

    def _append_jsonl(self, path: str, obj: dict[str, Any]) -> None:
        line = json.dumps(obj, default=str) + "\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)

    async def start(self, page: Page, *, snapshots_only: bool = False) -> None:
        if not tracing_enabled():
            return
        self._snapshots_only = snapshots_only
        self._mkdirs()
        self._page = page
        self._started_at = time.time()
        self._started = True
        if not snapshots_only:
            page.on("console", self._on_console)
            page.on("request", self._on_request)
            page.on("response", self._on_response_net)
        self._stop.clear()
        self._shot_task = asyncio.create_task(self._screenshot_loop())
        logger.info("Snapshot tracer started (snapshots_only=%s): %s", snapshots_only, self.root)

    def _on_console(self, msg: Any) -> None:
        try:
            self._append_jsonl(
                self._console_path,
                {
                    "t": datetime.now(timezone.utc).isoformat(),
                    "type": getattr(msg, "type", ""),
                    "text": getattr(msg, "text", str(msg)),
                },
            )
        except Exception as e:
            logger.debug("console log: %s", e)

    def _on_request(self, request: Any) -> None:
        try:
            self._append_jsonl(
                self._net_path,
                {
                    "phase": "request",
                    "t": datetime.now(timezone.utc).isoformat(),
                    "url": request.url,
                    "method": request.method,
                },
            )
        except Exception as e:
            logger.debug("net request log: %s", e)

    def _on_response_net(self, response: Any) -> None:
        try:
            self._append_jsonl(
                self._net_path,
                {
                    "phase": "response",
                    "t": datetime.now(timezone.utc).isoformat(),
                    "url": response.url,
                    "status": response.status,
                },
            )
        except Exception as e:
            logger.debug("net response log: %s", e)

    async def _screenshot_loop(self) -> None:
        interval = 2.5
        seq = 0
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                pass
            if self._page is None or self._page.is_closed():
                break
            try:
                path = os.path.join(self.screens_dir, f"shot_{seq:04d}.png")
                await self._page.screenshot(path=path, full_page=False)
                self._shot_count += 1
                dom_path = os.path.join(self.dom_dir, f"snapshot_{seq:04d}.html")
                content = await self._page.content()
                with open(dom_path, "w", encoding="utf-8") as f:
                    f.write(content)
                seq += 1
            except Exception as e:
                logger.debug("screenshot/dom snapshot: %s", e)

    async def stop(self) -> None:
        if not tracing_enabled() or not self._started:
            return
        self._stop.set()
        if self._shot_task:
            self._shot_task.cancel()
            try:
                await self._shot_task
            except asyncio.CancelledError:
                pass
            self._shot_task = None
        if self._page:
            try:
                if not self._snapshots_only:
                    self._page.remove_listener("console", self._on_console)
                    self._page.remove_listener("request", self._on_request)
                    self._page.remove_listener("response", self._on_response_net)
            except Exception:
                pass
        summary = {
            "run_id": self.run_id,
            "duration_ms": int((time.time() - self._started_at) * 1000),
            "screenshots": self._shot_count,
            "snapshots_only": self._snapshots_only,
            "failure_triggered_tracing": self._snapshots_only,
        }
        try:
            with open(self._summary_path, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2)
        except Exception as e:
            logger.warning("summary write: %s", e)
        self._started = False
        self._page = None
        self._snapshots_only = False
