import asyncio
import logging
from typing import Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright

from .base import BaseProvider, ChatResponse

logger = logging.getLogger(__name__)

BASE_URL = "https://claude.ai"
NEW_CHAT_URL = f"{BASE_URL}/new"


class ClaudeProvider(BaseProvider):
    name = "claude"

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
            launch_args = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
            self._browser = await self._playwright.chromium.launch(**launch_args)

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
        await self._page.goto(BASE_URL, wait_until="networkidle")
        return await self.check_session()

    async def send_message(self, message: str, conversation_id: Optional[str] = None) -> ChatResponse:
        await self._ensure_browser()

        if conversation_id:
            url = f"{BASE_URL}/chat/{conversation_id}"
        else:
            url = NEW_CHAT_URL

        await self._page.goto(url, wait_until="networkidle")
        await asyncio.sleep(1)

        # Type into the composer
        composer = self._page.locator('div[contenteditable="true"]').first
        await composer.wait_for(state="visible", timeout=15000)
        await composer.click()
        await composer.fill("")
        await composer.type(message, delay=10)
        await asyncio.sleep(0.3)

        # Submit
        send_btn = self._page.locator('button[aria-label*="Send"]').first
        await send_btn.click()

        response_text = await self.get_response()

        # Extract conversation ID from URL
        current_url = self._page.url
        conv_id = current_url.split("/chat/")[-1].split("?")[0] if "/chat/" in current_url else None

        return ChatResponse(
            provider=self.name,
            message=response_text,
            conversation_id=conv_id,
            model="claude",
        )

    async def get_response(self) -> str:
        # Wait for the stop button to appear (generation started)
        try:
            await self._page.wait_for_selector('button[aria-label*="Stop"]', timeout=15000)
        except Exception:
            pass

        # Wait for the stop button to disappear (generation finished)
        try:
            await self._page.wait_for_selector('button[aria-label*="Stop"]', state="hidden", timeout=120000)
        except Exception:
            pass

        await asyncio.sleep(0.5)

        # Get the last assistant message
        messages = await self._page.locator('[data-testid="assistant-message"]').all()
        if not messages:
            # Fallback selector
            messages = await self._page.locator('.font-claude-message').all()

        if messages:
            return (await messages[-1].inner_text()).strip()
        return ""

    async def check_session(self) -> bool:
        try:
            await self._ensure_browser()
            await self._page.goto(BASE_URL, wait_until="networkidle")
            # Logged-in users see new chat button; logged-out see login
            login_visible = await self._page.locator('text="Log in"').count() > 0
            return not login_visible
        except Exception as e:
            logger.warning("Claude session check failed: %s", e)
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
