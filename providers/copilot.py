import asyncio
import logging
from typing import Optional
from urllib.parse import urlparse

from playwright.async_api import Page

from core.traced_send import send_with_optional_failure_trace

from .base import ChatResponse
from .playwright_bases import ChromiumTracingPlaywrightProvider

logger = logging.getLogger(__name__)

BASE_URL = "https://copilot.microsoft.com"


def _copilot_explicitly_logged_out(page: Page) -> bool:
    u = page.url.lower()
    netloc = urlparse(u).netloc.lower()
    if "login.microsoftonline.com" in netloc or "login.live.com" in netloc:
        return True
    if "copilot.microsoft.com" not in netloc and "bing.com" not in netloc:
        return True
    path = urlparse(u).path.lower()
    if "/login" in path or "/signin" in path:
        return True
    return False


def _copilot_on_app_surface(url: str) -> bool:
    n = urlparse(url).netloc.lower()
    return "copilot.microsoft.com" in n or "bing.com" in n


class CopilotProvider(ChromiumTracingPlaywrightProvider):
    name = "copilot"
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
            await page.goto(BASE_URL, wait_until="domcontentloaded")
            await asyncio.sleep(1.5)

            composer = page.locator('textarea[placeholder], [contenteditable="true"]').first
            await composer.wait_for(state="visible", timeout=15000)
            await composer.click()
            await composer.fill(message)
            await asyncio.sleep(0.3)

            await page.keyboard.press("Enter")

            response_text = await self._get_response_on_page(page)

            return ChatResponse(
                provider=self.name,
                message=response_text,
                conversation_id=conversation_id,
                model="copilot",
            )

        return await send_with_optional_failure_trace(
            page=page,
            page_url_hint="copilot.microsoft.com",
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
            loc = self._page.locator('textarea[placeholder], [contenteditable="true"]').first
            try:
                await loc.wait_for(state="visible", timeout=10_000)
                return True
            except Exception:
                pass
            if _copilot_explicitly_logged_out(self._page):
                return False
            if _copilot_on_app_surface(self._page.url):
                logger.info("Copilot: composer not visible in probe; treating session as alive on app host")
                return True
            return False
        except Exception as e:
            logger.warning("Copilot session check failed: %s", e)
            return False
