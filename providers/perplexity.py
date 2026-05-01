import asyncio
import logging
from typing import Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright

from browser.tracing_launch import chromium_tracing_args
from core.traced_send import send_with_optional_failure_trace

from .base import BaseProvider, ChatResponse

logger = logging.getLogger(__name__)

BASE_URL = "https://www.perplexity.ai"


class PerplexityProvider(BaseProvider):
    name = "perplexity"

    def __init__(self, cookies: Optional[list] = None, storage_state: Optional[str] = None):
        self._cookies = cookies or []
        self._storage_state = storage_state
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    async def _ensure_browser(self) -> None:
        if self._browser is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", *chromium_tracing_args()],
            )
        if self._context is None:
            context_opts = {}
            if self._storage_state:
                context_opts["storage_state"] = self._storage_state
            self._context = await self._browser.new_context(**context_opts)
            if self._cookies:
                await self._context.add_cookies(self._cookies)
        if self._page is None or self._page.is_closed():
            self._page = await self._context.new_page()

    async def login(self) -> bool:
        await self._ensure_browser()
        await self._page.goto(BASE_URL, wait_until="domcontentloaded")
        return await self.check_session()

    async def send_message(self, message: str, conversation_id: Optional[str] = None) -> ChatResponse:
        await self._ensure_browser()
        page = self._page

        async def _attempt() -> ChatResponse:
            url = f"{BASE_URL}/search/{conversation_id}" if conversation_id else BASE_URL
            await page.goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(1)

            composer = page.locator('textarea[placeholder*="Ask"]').first
            await composer.wait_for(state="visible", timeout=15000)
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

    async def get_response(self) -> str:
        await self._ensure_browser()
        return await self._get_response_on_page(self._page)
        try:
            await self._ensure_browser()
            await self._page.goto(BASE_URL, wait_until="domcontentloaded")
            signed_out = await self._page.locator('text="Sign up"').count() > 0
            return not signed_out
        except Exception as e:
            logger.warning("Perplexity session check failed: %s", e)
            return False

    async def reset_session(self) -> bool:
        await self.close()
        self._context = None
        self._page = None
        return await self.login()

    async def close(self) -> None:
        if self._page and not self._page.is_closed():
            await self._page.close()
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
