import asyncio
import logging
import re
import time
from typing import Optional
from urllib.parse import urlparse

from playwright.async_api import Page

from browser.generic_network_collector import GenericProviderNetworkCollector
from core.traced_send import send_with_optional_failure_trace
from parsers.generic_parser import parse_generic_turn

from .base import ChatResponse, PlaywrightProviderBase

logger = logging.getLogger(__name__)

BASE_URL = "https://copilot.microsoft.com"

COMPOSER_SELECTORS = (
    "cib-serp-chat cib-text-input textarea",
    "cib-text-input textarea",
    "textarea.cib-text-area",
    'textarea[placeholder]',
    "main textarea",
    '[contenteditable="true"]',
)


def _copilot_network_url(url: str) -> bool:
    u = url.lower()
    if "sydney.bing.com" in u:
        return True
    if "copilot.microsoft.com" in u and "/c4/api" in u:
        return True
    if "copilot.microsoft.com" in u and "api" in u:
        return True
    if "bing.com" in u and ("copilot" in u or "sydney" in u):
        return True
    return False


def _copilot_explicitly_logged_out(page: Page) -> bool:
    u = page.url.lower()
    netloc = urlparse(u).netloc.lower()
    if "login.microsoftonline.com" in netloc or "login.live.com" in netloc:
        return True
    path = urlparse(u).path.lower()
    if "/login" in path or "/signin" in path:
        return True
    return False


def _copilot_on_app_surface(url: str) -> bool:
    n = urlparse(url).netloc.lower()
    return "copilot.microsoft.com" in n or "bing.com" in n


class CopilotProvider(PlaywrightProviderBase):
    name = "copilot"
    BASE_URL = BASE_URL
    USE_STEALTH = False

    async def _extract_dom_assistant_text(self, page: Page) -> str:
        try:
            await page.wait_for_selector('[aria-label*="Stop"]', timeout=15000)
        except Exception:
            pass
        try:
            await page.wait_for_selector('[aria-label*="Stop"]', state="hidden", timeout=120000)
        except Exception:
            pass
        await asyncio.sleep(0.5)
        messages = await page.locator('[class*="message"][class*="bot"], [data-testid*="assistant"]').all()
        if not messages:
            messages = await page.locator('cib-message-group[source="bot"] cib-message').all()
        if messages:
            return (await messages[-1].inner_text()).strip()
        return ""

    async def check_session(self) -> bool:
        try:
            await self._ensure_browser()
            await self._page.goto(BASE_URL, wait_until="domcontentloaded")
            await asyncio.sleep(1.5)
            if _copilot_explicitly_logged_out(self._page):
                return False
            if await self.snapshot_challenge_or_bot_wall(self._page):
                return False
            try:
                await self._wait_first_visible_composer(self._page, COMPOSER_SELECTORS, timeout_ms=12_000)
                return True
            except Exception:
                pass
            if _copilot_explicitly_logged_out(self._page):
                return False
            if _copilot_on_app_surface(self._page.url):
                logger.info("Copilot: composer probe inconclusive; treating as alive on app host")
                return True
            return False
        except Exception as e:
            logger.warning("Copilot session check failed: %s", e)
            return False

    async def send_message(self, message: str, conversation_id: Optional[str] = None) -> ChatResponse:
        async with self._request_lock:
            await self._ensure_browser()
            trace_page = self._page

            async def _attempt() -> ChatResponse:
                page = trace_page
                collector = GenericProviderNetworkCollector(page, _copilot_network_url)
                collector.clear()
                collector.attach()
                started = time.perf_counter()
                try:
                    await page.goto(BASE_URL, wait_until="domcontentloaded")
                    try:
                        await page.wait_for_load_state("load", timeout=30_000)
                    except Exception:
                        pass
                    try:
                        await page.wait_for_load_state("networkidle", timeout=25_000)
                    except Exception:
                        pass
                    await asyncio.sleep(2.0)
                    for pattern in (
                        r"Accept all",
                        r"^Accept$",
                        r"Continue",
                        r"Dismiss",
                        r"Not now",
                        r"Maybe later",
                        r"No thanks",
                    ):
                        try:
                            b = page.get_by_role("button", name=re.compile(pattern, re.I)).first
                            if await b.is_visible(timeout=600):
                                await b.click(timeout=2000)
                                await asyncio.sleep(0.4)
                        except Exception:
                            pass
                    try:
                        await page.evaluate(
                            "window.scrollTo(0, Math.max(0, document.body.scrollHeight - 200))"
                        )
                    except Exception:
                        pass
                    composer = await self._wait_first_visible_composer(page, COMPOSER_SELECTORS, timeout_ms=60_000)
                    await composer.click()
                    await composer.fill(message)
                    await asyncio.sleep(0.3)
                    await page.keyboard.press("Enter")
                    await asyncio.sleep(0.5)
                finally:
                    await collector.wait_for_pending(3.0)
                    collector.detach()
                records = collector.get_records()
                dom_text = await self._extract_dom_assistant_text(page)
                parsed = parse_generic_turn(
                    provider=self.name,
                    network_records=records,
                    dom_text=dom_text,
                    started_perf=started,
                )
                if parsed.status != "success" or not (parsed.text or "").strip():
                    raise RuntimeError(
                        f"parse_failed source={parsed.source} errors={parsed.errors!r} dom_len={len(dom_text)}"
                    )
                return ChatResponse(
                    provider=self.name,
                    message=parsed.text.strip(),
                    conversation_id=conversation_id,
                    model="copilot",
                )

            return await send_with_optional_failure_trace(
                page=trace_page,
                page_url_hint="copilot.microsoft.com",
                send_impl=_attempt,
            )
