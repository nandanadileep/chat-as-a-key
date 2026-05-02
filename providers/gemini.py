import asyncio
import logging
import re
from typing import Optional

from playwright.async_api import Page

from core.traced_send import send_with_optional_failure_trace

from .base import ChatResponse
from .playwright_bases import ChromiumTracingPlaywrightProvider

logger = logging.getLogger(__name__)

BASE_URL = "https://gemini.google.com"


class GeminiProvider(ChromiumTracingPlaywrightProvider):
    name = "gemini"
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
            url = f"{BASE_URL}/app/{conversation_id}" if conversation_id else BASE_URL
            await page.goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(2.5)

            composer = page.locator(
                "rich-textarea p, "
                'rich-textarea [contenteditable="true"], '
                'div.ql-editor[contenteditable="true"], '
                'div[contenteditable="true"][role="textbox"]'
            ).first
            await composer.wait_for(state="visible", timeout=45_000)
            await composer.click()
            await composer.type(message, delay=10)
            await asyncio.sleep(0.3)

            await page.keyboard.press("Enter")

            response_text = await self._get_response_on_page(page)

            current_url = page.url
            conv_id = current_url.split("/app/")[-1].split("?")[0] if "/app/" in current_url else None

            return ChatResponse(
                provider=self.name,
                message=response_text,
                conversation_id=conv_id,
                model="gemini",
            )

        return await send_with_optional_failure_trace(
            page=page,
            page_url_hint="gemini.google.com",
            send_impl=_attempt,
        )

    async def _get_response_on_page(self, page: Page) -> str:
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

            for sel in (
                "rich-textarea p",
                'rich-textarea [contenteditable="true"]',
                'div.ql-editor[contenteditable="true"]',
            ):
                loc = self._page.locator(sel).first
                try:
                    if await loc.is_visible(timeout=5000):
                        return True
                except Exception:
                    continue

            try:
                if await self._page.get_by_role("button", name=re.compile(r"sign in to google", re.I)).first.is_visible(
                    timeout=2000
                ):
                    return False
            except Exception:
                pass
            return False
        except Exception as e:
            logger.warning("Gemini session check failed: %s", e)
            return False
