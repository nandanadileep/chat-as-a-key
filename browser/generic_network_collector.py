"""Capture JSON/SSE from arbitrary provider API traffic (same pattern as ClaudeNetworkCollector)."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Set

from playwright.async_api import Page, Response

logger = logging.getLogger(__name__)


class GenericProviderNetworkCollector:
    """Attaches a response listener; keeps records whose URL passes ``url_matcher``."""

    def __init__(
        self,
        page: Page,
        url_matcher: Callable[[str], bool],
        *,
        on_record: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> None:
        self._page = page
        self._url_matcher = url_matcher
        self._records: list[dict[str, Any]] = []
        self._on_record = on_record
        self._pending: Set[asyncio.Task[Any]] = set()
        self._attached = False

    def clear(self) -> None:
        self._records.clear()

    def get_records(self) -> list[dict[str, Any]]:
        return list(self._records)

    def attach(self) -> None:
        if self._attached:
            return
        self._page.on("response", self._sync_response_handler)
        self._attached = True

    def detach(self) -> None:
        if not self._attached:
            return
        try:
            self._page.remove_listener("response", self._sync_response_handler)
        except Exception as e:
            logger.debug("remove_listener response: %s", e)
        self._attached = False

    def _sync_response_handler(self, response: Response) -> None:
        task = asyncio.create_task(self._handle_response(response))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _handle_response(self, response: Response) -> None:
        url = response.url
        if not self._url_matcher(url):
            return
        try:
            ct = (response.headers or {}).get("content-type", "") or ""
        except Exception:
            ct = ""
        if "json" not in ct.lower() and "text/event-stream" not in ct.lower() and "plain" not in ct.lower():
            return
        try:
            body = await response.text()
        except Exception as e:
            logger.debug("response.text failed %s: %s", url[:120], e)
            return
        raw_json: Any = None
        response_text = body
        try:
            raw_json = json.loads(body)
            if not isinstance(raw_json, (dict, list)):
                raw_json = None
        except json.JSONDecodeError:
            raw_json = None
        rec = {
            "url": url,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": response.status,
            "content_type": ct,
            "response_text": response_text[:500_000],
            "raw_json": raw_json,
        }
        self._records.append(rec)
        if self._on_record:
            try:
                self._on_record(rec)
            except Exception as e:
                logger.debug("on_record callback: %s", e)
        logger.debug("Captured network response %s bytes from %s", len(body), url[:100])

    async def wait_for_pending(self, timeout: float = 2.0) -> None:
        if not self._pending:
            return
        pending = set(self._pending)
        _, still = await asyncio.wait(pending, timeout=timeout)
        for p in still:
            p.cancel()
