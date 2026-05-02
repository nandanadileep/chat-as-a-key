import asyncio
import logging
import re
import time
from typing import Optional

from playwright.async_api import Page

from browser.generic_network_collector import GenericProviderNetworkCollector
from core.traced_send import send_with_optional_failure_trace
from parsers.generic_parser import parse_generic_turn

from .base import ChatResponse, PlaywrightProviderBase

logger = logging.getLogger(__name__)

BASE_URL = "https://gemini.google.com"

COMPOSER_SELECTORS = (
    "rich-textarea p",
    'rich-textarea [contenteditable="true"]',
    'div.ql-editor[contenteditable="true"]',
    'div[contenteditable="true"][role="textbox"]',
    "main textarea",
)


def _gemini_network_url(url: str) -> bool:
    u = url.lower()
    if "generativelanguage.googleapis.com" in u:
        return True
    if "batchrunquery" in u:
        return True
    if "google.com" in u and any(x in u for x in ("gemini", "bard", "ai-studio", "/v1/")):
        return True
    return False


class GeminiProvider(PlaywrightProviderBase):
    name = "gemini"
    BASE_URL = BASE_URL
    USE_STEALTH = False

    async def _extract_dom_assistant_text(self, page: Page) -> str:
        try:
            await page.wait_for_selector('.loading-indicator, [aria-label*="Stop"]', timeout=15000)
        except Exception:
            pass
        try:
            await page.wait_for_selector('.loading-indicator, [aria-label*="Stop"]', state="hidden", timeout=120000)
        except Exception:
            pass
        await asyncio.sleep(0.5)
        messages = await page.locator("model-response .markdown").all()
        if messages:
            return (await messages[-1].inner_text()).strip()
        return ""

    async def check_session(self) -> bool:
        try:
            await self._ensure_browser()
            await self._page.goto(BASE_URL, wait_until="domcontentloaded")
            await asyncio.sleep(2.5)
            url = self._page.url.lower()
            if "accounts.google.com" in url:
                return False
            if await self.snapshot_challenge_or_bot_wall(self._page):
                return False
            try:
                await self._wait_first_visible_composer(self._page, COMPOSER_SELECTORS, timeout_ms=10_000)
                return True
            except Exception:
                pass
            try:
                if await self._page.get_by_role("button", name=re.compile(r"sign in to google", re.I)).first.is_visible(
                    timeout=2000
                ):
                    return False
            except Exception:
                pass
            if "gemini.google.com" in url:
                logger.info("Gemini: composer probe inconclusive; treating as alive on product host")
                return True
            return False
        except Exception as e:
            logger.warning("Gemini session check failed: %s", e)
            return False

    async def send_message(self, message: str, conversation_id: Optional[str] = None) -> ChatResponse:
        async with self._request_lock:

            async def _attempt() -> ChatResponse:
                await self._ensure_browser()
                page = self._page
                collector = GenericProviderNetworkCollector(page, _gemini_network_url)
                collector.clear()
                collector.attach()
                started = time.perf_counter()
                try:
                    url = f"{BASE_URL}/app/{conversation_id}" if conversation_id else BASE_URL
                    await page.goto(url, wait_until="domcontentloaded")
                    await asyncio.sleep(2.5)
                    composer = await self._wait_first_visible_composer(page, COMPOSER_SELECTORS, timeout_ms=45_000)
                    await composer.click()
                    await composer.type(message, delay=10)
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
                conv_id = current_url.split("/app/")[-1].split("?")[0] if "/app/" in current_url else None
                return ChatResponse(
                    provider=self.name,
                    message=parsed.text.strip(),
                    conversation_id=conv_id,
                    model="gemini",
                )

            return await send_with_optional_failure_trace(
                page=self._page,
                page_url_hint="gemini.google.com",
                send_impl=_attempt,
            )
