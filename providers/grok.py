import asyncio
import logging
from typing import Optional
from urllib.parse import urlparse

from playwright.async_api import Page

from core.traced_send import send_with_optional_failure_trace

from .base import ChatResponse
from .playwright_bases import ChromiumTracingPlaywrightProvider

logger = logging.getLogger(__name__)

BASE_URL = "https://grok.com"


def _grok_explicitly_logged_out(page: Page) -> bool:
    u = page.url.lower()
    netloc = urlparse(u).netloc.lower()
    path = urlparse(u).path.lower()
    if netloc != "grok.com" and not netloc.endswith(".grok.com"):
        return True
    if "/login" in path or "/signin" in path or "/sign-in" in path:
        return True
    return False


def _grok_on_chat_surface(url: str) -> bool:
    n = urlparse(url).netloc.lower()
    if n != "grok.com" and not n.endswith(".grok.com"):
        return False
    path = urlparse(url).path.lower()
    return path == "/" or path.startswith("/chat")


class GrokProvider(ChromiumTracingPlaywrightProvider):
    name = "grok"
    BASE_URL = BASE_URL

    def __init__(
        self,
        cookies: Optional[list] = None,
        storage_state: Optional[str] = None,
        *,
        playwright_headless: bool = True,
    ) -> None:
        super().__init__(cookies, storage_state, headless=playwright_headless)

    async def send_message(self, message: str, conversation_id: Optional[str] = None) -> ChatResponse:
        await self._ensure_browser()
        page = self._page

        async def _attempt() -> ChatResponse:
            url = f"{BASE_URL}/chat/{conversation_id}" if conversation_id else BASE_URL
            await page.goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(1)

            composer = page.locator('textarea[placeholder], [contenteditable="true"]').first
            await composer.wait_for(state="visible", timeout=15000)
            await composer.click()
            await composer.fill(message)
            await asyncio.sleep(0.3)

            await page.keyboard.press("Enter")

            response_text = await self._get_response_on_page(page)

            current_url = page.url
            conv_id = current_url.split("/chat/")[-1].split("?")[0] if "/chat/" in current_url else None

            return ChatResponse(
                provider=self.name,
                message=response_text,
                conversation_id=conv_id,
                model="grok",
            )

        return await send_with_optional_failure_trace(
            page=page,
            page_url_hint="grok.com",
            send_impl=_attempt,
        )

    async def _get_response_on_page(self, page: Page) -> str:
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
            loc = self._page.locator('textarea[placeholder], [contenteditable="true"]').first
            try:
                await loc.wait_for(state="visible", timeout=10_000)
                return True
            except Exception:
                pass
            if _grok_explicitly_logged_out(self._page):
                return False
            if _grok_on_chat_surface(self._page.url):
                logger.info("Grok: composer not visible in probe window; treating session as alive on app host")
                return True
            return False
        except Exception as e:
            logger.warning("Grok session check failed: %s", e)
            return False
