from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, Awaitable, Callable, Optional

from playwright.async_api import Page

from browser.cdp_observer import CdpReadOnlyObserver
from browser.network_interceptor import ClaudeNetworkCollector
from browser.session_manager import BrowserSessionManager
from browser.tracer import ObservabilityTracer, tracing_enabled
from parsers.claude_parser import ClaudeParseResult, parse_claude_turn
from providers.base import ChatResponse

logger = logging.getLogger(__name__)

BASE_URL = "https://claude.ai"
NEW_CHAT_URL = f"{BASE_URL}/new"

COMPOSER_SELECTORS = [
    'div.ProseMirror[contenteditable="true"]',
    '[data-testid="chat-input"] [contenteditable="true"]',
    'div[contenteditable="true"]',
]
SEND_SELECTORS = [
    'button[aria-label="Send message"]',
    'button[aria-label*="Send"]',
    'button[data-testid="send-button"]',
]
RESPONSE_SELECTORS = [
    '[data-testid="assistant-message"]',
    '.font-claude-message',
    '[class*="AssistantMessage"]',
]


class ClaudeOrchestrator:
    def __init__(self, session: BrowserSessionManager) -> None:
        self._session = session
        self._collector: Optional[ClaudeNetworkCollector] = None

    def _run_dir(self, run_id: str) -> str:
        return os.path.join(".o11y", run_id)

    def _append_error(self, run_id: str, event: dict[str, Any]) -> None:
        path = os.path.join(self._run_dir(run_id), "errors.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        events: list[Any] = []
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    events = json.load(f)
                if not isinstance(events, list):
                    events = []
            except Exception:
                events = []
        events.append(event)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(events, f, indent=2, default=str)
        except Exception as e:
            logger.warning("errors.json write failed: %s", e)

    @staticmethod
    def _turn_successful(parsed: ClaudeParseResult) -> bool:
        return parsed.status == "success" and bool((parsed.text or "").strip())

    @staticmethod
    def _needs_failure_traced_retry(
        parsed: Optional[ClaudeParseResult],
        exc: Optional[Exception],
        dom_text: str,
    ) -> bool:
        if exc is not None:
            return True
        if parsed is None:
            return True
        if parsed.status != "success":
            return True
        if not (parsed.text or "").strip():
            return True
        if parsed.source == "network" and not (dom_text or "").strip():
            return True
        return False

    async def _find_composer(self, page: Page) -> Any:
        for sel in COMPOSER_SELECTORS:
            loc = page.locator(sel).first
            try:
                await loc.wait_for(state="visible", timeout=8000)
                return loc
            except Exception:
                continue
        raise RuntimeError(f"Composer not found. Tried: {COMPOSER_SELECTORS}")

    async def _find_send_button(self, page: Page) -> Any:
        for sel in SEND_SELECTORS:
            loc = page.locator(sel).first
            try:
                await loc.wait_for(state="visible", timeout=5000)
                return loc
            except Exception:
                continue
        raise RuntimeError("Send button not found")

    async def _dom_response_text(self, page: Page) -> str:
        try:
            await page.wait_for_selector(
                'button[aria-label*="Stop"], button[data-testid="stop-button"]',
                timeout=20000,
            )
        except Exception:
            pass
        try:
            await page.wait_for_selector(
                'button[aria-label*="Stop"], button[data-testid="stop-button"]',
                state="hidden",
                timeout=120000,
            )
        except Exception:
            pass
        await asyncio.sleep(0.5)
        for sel in RESPONSE_SELECTORS:
            messages = await page.locator(sel).all()
            if messages:
                return (await messages[-1].inner_text()).strip()
        return ""

    async def _navigate_chat(self, page: Page, conversation_id: Optional[str]) -> None:
        url = f"{BASE_URL}/chat/{conversation_id}" if conversation_id else NEW_CHAT_URL
        await page.goto(url, wait_until="domcontentloaded")
        await asyncio.sleep(2)

    async def _send_typed_turn(
        self,
        page: Page,
        message: str,
        conversation_id: Optional[str],
        run_id: str,
    ) -> tuple[list[dict[str, Any]], str, list[str]]:
        errs: list[str] = []
        if self._collector:
            self._collector.detach()
        self._collector = ClaudeNetworkCollector(page)
        self._collector.attach()
        self._collector.clear()

        try:
            await self._navigate_chat(page, conversation_id)
        except Exception as e:
            errs.append(f"navigation:{e}")
            self._append_error(run_id, {"phase": "navigate", "error": str(e), "t": time.time()})
            raise

        composer = await self._find_composer(page)
        await composer.click()
        await composer.fill("")
        await page.keyboard.type(message, delay=12)
        await asyncio.sleep(0.25)
        send_btn = await self._find_send_button(page)
        await send_btn.click()

        await asyncio.sleep(1.0)
        if self._collector:
            await self._collector.wait_for_pending(3.0)
        records = self._collector.get_records() if self._collector else []
        dom_text = await self._dom_response_text(page)
        return records, dom_text, errs

    async def _run_one_send_and_parse(
        self,
        page: Page,
        message: str,
        conversation_id: Optional[str],
        run_id: str,
        started_perf: float,
    ) -> tuple[Optional[ClaudeParseResult], list[dict[str, Any]], str, list[str], Optional[Exception]]:
        try:
            records, dom_text, errs = await self._send_typed_turn(
                page, message, conversation_id, run_id
            )
        except Exception as e:
            return None, [], "", [str(e)], e
        parsed = parse_claude_turn(
            provider="claude",
            network_records=records,
            dom_text=dom_text,
            started_perf=started_perf,
            errors=errs,
        )
        return parsed, records, dom_text, errs, None

    def _response_from_page(self, page: Page, message_text: str) -> ChatResponse:
        current_url = page.url
        conv_id = current_url.split("/chat/")[-1].split("?")[0] if "/chat/" in current_url else None
        return ChatResponse(
            provider="claude",
            message=message_text,
            conversation_id=conv_id,
            model="claude",
        )

    def _log_parsed_success(self, parsed: ClaudeParseResult, run_id: str) -> None:
        if parsed.source == "network":
            logger.info(
                "Claude reply source=network latency_ms=%s run_id=%s",
                parsed.latency_ms,
                run_id,
            )
        else:
            logger.info(
                "Claude reply source=dom fallback latency_ms=%s run_id=%s",
                parsed.latency_ms,
                run_id,
            )

    async def send_message(
        self,
        message: str,
        conversation_id: Optional[str] = None,
        *,
        check_session_cb: Callable[[], Awaitable[bool]],
        reset_session_cb: Callable[[], Awaitable[bool]],
    ) -> ChatResponse:
        run_id = str(uuid.uuid4())
        os.makedirs(self._run_dir(run_id), exist_ok=True)
        page = await self._session.ensure_browser()
        started_perf = time.perf_counter()
        last_exc: Optional[Exception] = None
        last_parsed: Optional[ClaudeParseResult] = None
        last_dom = ""

        for attempt in range(2):
            parsed, _records, dom_text, _errs, exc = await self._run_one_send_and_parse(
                page, message, conversation_id, run_id, started_perf
            )
            last_parsed = parsed
            last_dom = dom_text
            last_exc = exc

            if exc is not None:
                self._append_error(run_id, {"phase": "send", "attempt": attempt, "error": str(exc)})
                if attempt == 0:
                    try:
                        await page.reload(wait_until="domcontentloaded")
                        await asyncio.sleep(2)
                    except Exception:
                        pass
                continue

            if parsed is not None and self._turn_successful(parsed):
                self._log_parsed_success(parsed, run_id)
                return self._response_from_page(page, parsed.text)

            if parsed is not None:
                self._append_error(
                    run_id,
                    {"phase": "empty_or_parse", "attempt": attempt, "errors": parsed.errors},
                )
            if attempt == 0:
                logger.warning("Empty or weak response — reloading page once (run_id=%s)", run_id)
                try:
                    await page.reload(wait_until="domcontentloaded")
                    await asyncio.sleep(2)
                except Exception as e:
                    self._append_error(run_id, {"phase": "reload", "error": str(e)})

        if tracing_enabled() and self._needs_failure_traced_retry(last_parsed, last_exc, last_dom):
            logger.info("Retrying with tracing enabled (run_id=%s)", run_id)
            cdp = CdpReadOnlyObserver(run_id, page_url_hint="claude.ai")
            obs = ObservabilityTracer(run_id)
            try:
                try:
                    await cdp.start()
                except Exception as e:
                    logger.warning("CDP observer start failed (ignored): %s", e)
                try:
                    await obs.start(page, snapshots_only=True)
                except Exception as e:
                    logger.warning("Snapshot tracer start failed (ignored): %s", e)

                parsed, _r, dom_text, _e, exc = await self._run_one_send_and_parse(
                    page, message, conversation_id, run_id, started_perf
                )
                last_parsed = parsed
                last_dom = dom_text
                last_exc = exc

                if exc is None and parsed is not None and self._turn_successful(parsed):
                    self._log_parsed_success(parsed, run_id)
                    return self._response_from_page(page, parsed.text)
                if exc is not None:
                    self._append_error(run_id, {"phase": "traced_retry_send", "error": str(exc)})
                elif parsed is not None:
                    self._append_error(
                        run_id,
                        {"phase": "traced_retry_parse", "errors": parsed.errors},
                    )
            except Exception as e:
                logger.warning("Failure-traced retry wrapper error (ignored): %s", e)
            finally:
                try:
                    await obs.stop()
                except Exception:
                    pass
                try:
                    await cdp.stop()
                except Exception:
                    pass

        live = await check_session_cb()
        if not live:
            logger.info("Session invalid after failures — resetting context (run_id=%s)", run_id)
            ok = await reset_session_cb()
            if ok:
                page = await self._session.ensure_browser()
                try:
                    parsed, _r, _d, _er, exc = await self._run_one_send_and_parse(
                        page, message, conversation_id, run_id, started_perf
                    )
                    last_exc = exc
                    if exc is None and parsed is not None and self._turn_successful(parsed):
                        self._log_parsed_success(parsed, run_id)
                        return self._response_from_page(page, parsed.text)
                except Exception as e:
                    last_exc = e
                    self._append_error(run_id, {"phase": "post_reset_send", "error": str(e)})

        raise RuntimeError(str(last_exc) if last_exc else "Claude send failed after retries")
