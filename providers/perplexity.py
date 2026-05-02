import asyncio
import logging
import re
import time
from typing import Optional
from urllib.parse import urlparse

from playwright.async_api import Locator, Page

from core.traced_send import send_with_optional_failure_trace

from .base import ChatResponse
from .playwright_bases import StealthChromePlaywrightProvider

logger = logging.getLogger(__name__)

BASE_URL = "https://www.perplexity.ai"


def _ppl_ms_budget(end: float, cap: int = 6000) -> int:
    ms = int((end - time.monotonic()) * 1000)
    return max(400, min(cap, ms))


async def _resolve_perplexity_composer(page: Page, timeout_ms: int = 120_000) -> Locator:
    end = time.monotonic() + timeout_ms / 1000.0
    last_err: Optional[Exception] = None
    while time.monotonic() < end:
        candidates: list[Locator] = [
            page.get_by_placeholder(re.compile(r"ask|what|how|search|type|query|message|prompt", re.I)),
            page.locator("main textarea"),
            page.locator('div[contenteditable="true"][role="textbox"]'),
            page.locator('textarea[rows]'),
            page.locator("textarea"),
        ]
        for loc in candidates:
            if _ppl_ms_budget(end, 6000) < 800:
                break
            c = loc.first
            try:
                await c.wait_for(state="visible", timeout=_ppl_ms_budget(end, 6000))
                return c
            except Exception as e:
                last_err = e
        await asyncio.sleep(0.35)
    raise TimeoutError(f"Perplexity composer not visible: {last_err}")


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


class PerplexityProvider(StealthChromePlaywrightProvider):
    name = "perplexity"
    BASE_URL = BASE_URL
    HEADED_ENV_VAR = "PERPLEXITY_HEADED"

    async def send_message(self, message: str, conversation_id: Optional[str] = None) -> ChatResponse:
        await self._ensure_browser()
        page = self._page

        async def _attempt() -> ChatResponse:
            url = f"{BASE_URL}/search/{conversation_id}" if conversation_id else BASE_URL
            await page.goto(url, wait_until="domcontentloaded")
            try:
                await page.wait_for_load_state("load", timeout=30_000)
            except Exception:
                pass
            await asyncio.sleep(3.0)

            for pattern in (r"Accept all", r"^Accept$", r"Continue", r"Dismiss", r"Not now"):
                try:
                    b = page.get_by_role("button", name=re.compile(pattern, re.I)).first
                    if await b.is_visible(timeout=600):
                        await b.click(timeout=2000)
                        await asyncio.sleep(0.4)
                except Exception:
                    pass

            composer = await _resolve_perplexity_composer(page)
            await composer.click()
            await composer.fill(message)
            await asyncio.sleep(0.3)

            await page.keyboard.press("Enter")

            response_text = await self._get_response_on_page(page)

            current_url = page.url
            conv_id = current_url.split("/search/")[-1].split("?")[0] if "/search/" in current_url else None

            return ChatResponse(
                provider=self.name,
                message=response_text,
                conversation_id=conv_id,
                model="perplexity",
            )

        return await send_with_optional_failure_trace(
            page=page,
            page_url_hint="perplexity.ai",
            send_impl=_attempt,
        )

    async def _get_response_on_page(self, page: Page) -> str:
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
            try:
                await _resolve_perplexity_composer(self._page, timeout_ms=15_000)
                return True
            except Exception:
                pass
            if await _perplexity_explicitly_logged_out(self._page):
                return False
            if _perplexity_on_app_surface(self._page.url):
                logger.info(
                    "Perplexity: composer not visible within probe window; treating session as alive "
                    "(send_message will still wait full timeout)"
                )
                return True
            return False
        except Exception as e:
            logger.warning("Perplexity session check failed: %s", e)
            return False
