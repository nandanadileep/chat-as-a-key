import asyncio
import logging
import time
from typing import Optional
from urllib.parse import urlparse

from playwright.async_api import Page

from browser.generic_network_collector import GenericProviderNetworkCollector
from core.traced_send import send_with_optional_failure_trace
from parsers.generic_parser import parse_generic_turn

from .base import ChatResponse, PlaywrightProviderBase

logger = logging.getLogger(__name__)

BASE_URL = "https://grok.com"

COMPOSER_SELECTORS = (
    'textarea[placeholder]',
    '[contenteditable="true"]',
    'div.ProseMirror[contenteditable="true"]',
    "main textarea",
)


def _grok_network_url(url: str) -> bool:
    u = url.lower()
    if "api.x.ai" in u or "x.ai" in u and "/api" in u:
        return True
    if "grok.com" in u and "/api" in u:
        return True
    if "grok.com" in u and any(x in u for x in ("chat", "completion", "stream", "message")):
        return True
    return False


def _grok_explicitly_logged_out(page: Page) -> bool:
    u = page.url.lower()
    netloc = urlparse(u).netloc.lower()
    path = urlparse(u).path.lower()
    if netloc != "grok.com" and not netloc.endswith(".grok.com"):
        return True
    if "/login" in path or "/signin" in path:
        return True
    return False


def _grok_on_app_surface(url: str) -> bool:
    n = urlparse(url).netloc.lower()
    return n == "grok.com" or n.endswith(".grok.com")


class GrokProvider(PlaywrightProviderBase):
    name = "grok"
    BASE_URL = BASE_URL
    USE_STEALTH = False

    async def _extract_dom_assistant_text(self, page: Page) -> str:
        try:
            await page.wait_for_selector('[aria-label*="Stop"], .loading', timeout=15000)
        except Exception:
            pass
        try:
            await page.wait_for_selector('[aria-label*="Stop"], .loading', state="hidden", timeout=120000)
        except Exception:
            pass
        await asyncio.sleep(0.5)
        messages = await page.locator('[class*="message"][class*="assistant"], [data-role="assistant"]').all()
        if messages:
            return (await messages[-1].inner_text()).strip()
        return ""

    async def check_session(self) -> bool:
        try:
            await self._ensure_browser()
            await self._page.goto(BASE_URL, wait_until="domcontentloaded")
            await asyncio.sleep(1.5)
            if _grok_explicitly_logged_out(self._page):
                return False
            if await self.snapshot_challenge_or_bot_wall(self._page):
                return False
            try:
                await self._wait_first_visible_composer(self._page, COMPOSER_SELECTORS, timeout_ms=10_000)
                return True
            except Exception:
                pass
            if _grok_explicitly_logged_out(self._page):
                return False
            if _grok_on_app_surface(self._page.url):
                logger.info("Grok: composer probe inconclusive; treating as alive on grok.com")
                return True
            return False
        except Exception as e:
            logger.warning("Grok session check failed: %s", e)
            return False

    async def send_message(self, message: str, conversation_id: Optional[str] = None) -> ChatResponse:
        async with self._request_lock:

            async def _attempt() -> ChatResponse:
                await self._ensure_browser()
                page = self._page
                collector = GenericProviderNetworkCollector(page, _grok_network_url)
                collector.clear()
                collector.attach()
                started = time.perf_counter()
                try:
                    url = f"{BASE_URL}/chat/{conversation_id}" if conversation_id else BASE_URL
                    await page.goto(url, wait_until="domcontentloaded")
                    await asyncio.sleep(1.0)
                    composer = await self._wait_first_visible_composer(page, COMPOSER_SELECTORS, timeout_ms=20_000)
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
                current_url = page.url
                conv_id = current_url.split("/chat/")[-1].split("?")[0] if "/chat/" in current_url else None
                return ChatResponse(
                    provider=self.name,
                    message=parsed.text.strip(),
                    conversation_id=conv_id,
                    model="grok",
                )

            return await send_with_optional_failure_trace(
                page=self._page,
                page_url_hint="grok.com",
                send_impl=_attempt,
            )
