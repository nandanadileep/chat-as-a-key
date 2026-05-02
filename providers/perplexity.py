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

BASE_URL = "https://www.perplexity.ai"

COMPOSER_SELECTORS = (
    'textarea[placeholder*="Ask"]',
    'textarea[placeholder*="ask"]',
    'textarea[placeholder*="Search"]',
    "main textarea",
    'div[contenteditable="true"][role="textbox"]',
    'textarea[rows]',
    "textarea",
)

# Shown when placeholder text does not match our attribute selectors (locale / redesign).
PERPLEXITY_PLACEHOLDER_PATTERNS = (
    r"Ask anything",
    r"ask|what|how|search|type|query|message|prompt",
)


def _perplexity_network_url(url: str) -> bool:
    u = url.lower()
    if "/rest/v2" in u or "/socket.io" in u:
        return True
    if "perplexity.ai" in u and "/api" in u:
        return True
    if "pplx.ai" in u and any(x in u for x in ("api", "rest", "socket")):
        return True
    return False


async def _perplexity_explicitly_logged_out(page: Page) -> bool:
    u = page.url.lower()
    path = urlparse(u).path.lower()
    netloc = urlparse(u).netloc.lower()
    if netloc in ("accounts.google.com", "accounts.youtube.com"):
        return True
    if "/login" in path or "/signin" in path or "/sign-in" in path:
        return True
    return False


def _perplexity_on_app_surface(url: str) -> bool:
    n = urlparse(url).netloc.lower()
    return "perplexity.ai" in n or n.endswith(".perplexity.ai") or "pplx.ai" in n


class PerplexityProvider(PlaywrightProviderBase):
    name = "perplexity"
    BASE_URL = BASE_URL
    USE_STEALTH = True
    HEADED_ENV_VAR = "PERPLEXITY_HEADED"

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
        messages = await page.locator('.prose, [class*="answer"]').all()
        if messages:
            return (await messages[-1].inner_text()).strip()
        return ""

    async def check_session(self) -> bool:
        try:
            await self._ensure_browser()
            await self._page.goto(BASE_URL, wait_until="domcontentloaded")
            await asyncio.sleep(2.0)
            if await _perplexity_explicitly_logged_out(self._page):
                return False
            if await self.snapshot_challenge_or_bot_wall(self._page):
                return False
            try:
                await self._wait_first_visible_composer(
                    self._page,
                    COMPOSER_SELECTORS,
                    timeout_ms=12_000,
                    placeholder_patterns=PERPLEXITY_PLACEHOLDER_PATTERNS,
                )
                return True
            except Exception:
                pass
            if await _perplexity_explicitly_logged_out(self._page):
                return False
            if _perplexity_on_app_surface(self._page.url):
                logger.info("Perplexity: composer probe inconclusive; treating as alive")
                return True
            return False
        except Exception as e:
            logger.warning("Perplexity session check failed: %s", e)
            return False

    async def send_message(self, message: str, conversation_id: Optional[str] = None) -> ChatResponse:
        async with self._request_lock:
            await self._ensure_browser()
            trace_page = self._page

            async def _attempt() -> ChatResponse:
                page = trace_page
                collector = GenericProviderNetworkCollector(page, _perplexity_network_url)
                collector.clear()
                collector.attach()
                started = time.perf_counter()
                try:
                    url = f"{BASE_URL}/search/{conversation_id}" if conversation_id else BASE_URL
                    await page.goto(url, wait_until="domcontentloaded")
                    try:
                        await page.wait_for_load_state("load", timeout=30_000)
                    except Exception:
                        pass
                    await asyncio.sleep(2.0)
                    for pattern in (r"Accept all", r"^Accept$", r"Continue", r"Dismiss", r"Not now"):
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
                    composer = await self._wait_first_visible_composer(
                        page,
                        COMPOSER_SELECTORS,
                        timeout_ms=90_000,
                        placeholder_patterns=PERPLEXITY_PLACEHOLDER_PATTERNS,
                    )
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
                conv_id = current_url.split("/search/")[-1].split("?")[0] if "/search/" in current_url else None
                return ChatResponse(
                    provider=self.name,
                    message=parsed.text.strip(),
                    conversation_id=conv_id,
                    model="perplexity",
                )

            return await send_with_optional_failure_trace(
                page=trace_page,
                page_url_hint="perplexity.ai",
                send_impl=_attempt,
            )
