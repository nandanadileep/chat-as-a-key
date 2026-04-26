import asyncio
import logging
from typing import Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright
from playwright_stealth import Stealth

from .base import BaseProvider, ChatResponse

logger = logging.getLogger(__name__)

BASE_URL = "https://claude.ai"
NEW_CHAT_URL = f"{BASE_URL}/new"

# Ordered from most to least specific — first one that appears wins
COMPOSER_SELECTORS = [
    'div.ProseMirror[contenteditable="true"]',
    '[data-testid="chat-input"] [contenteditable="true"]',
    'div[contenteditable="true"]',
]

SEND_SELECTORS = [
    'button[aria-label="Send message"]',
    'button[aria-label*="Send"]',
    'button[data-testid="send-button"]',
]

RESPONSE_SELECTORS = [
    '[data-testid="assistant-message"]',
    '.font-claude-message',
    '[class*="AssistantMessage"]',
]


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
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
            )

        if self._context is None:
            context_opts = {
                "user_agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "viewport": {"width": 1280, "height": 800},
                "locale": "en-US",
            }
            if self._storage_state:
                context_opts["storage_state"] = self._storage_state
            self._context = await self._browser.new_context(**context_opts)
            if self._cookies:
                await self._context.add_cookies(self._cookies)

        if self._page is None or self._page.is_closed():
            self._page = await self._context.new_page()
            await Stealth().apply_stealth_async(self._page)
            self._page.set_default_navigation_timeout(60000)

    async def _find_composer(self) -> object:
        """Try each composer selector until one is visible."""
        for sel in COMPOSER_SELECTORS:
            loc = self._page.locator(sel).first
            try:
                await loc.wait_for(state="visible", timeout=5000)
                return loc
            except Exception:
                continue
        raise RuntimeError(f"Composer not found after trying: {COMPOSER_SELECTORS}")

    async def _find_send_button(self) -> object:
        for sel in SEND_SELECTORS:
            loc = self._page.locator(sel).first
            try:
                await loc.wait_for(state="visible", timeout=3000)
                return loc
            except Exception:
                continue
        raise RuntimeError("Send button not found")

    async def login(self) -> bool:
        await self._ensure_browser()
        await self._page.goto(BASE_URL, wait_until="domcontentloaded")
        return await self.check_session()

    async def send_message(self, message: str, conversation_id: Optional[str] = None) -> ChatResponse:
        await self._ensure_browser()

        url = f"{BASE_URL}/chat/{conversation_id}" if conversation_id else NEW_CHAT_URL
        await self._page.goto(url, wait_until="domcontentloaded")
        # Give React time to hydrate
        await asyncio.sleep(2)

        composer = await self._find_composer()
        await composer.click()
        await composer.fill("")
        await self._page.keyboard.type(message, delay=15)
        await asyncio.sleep(0.3)

        send_btn = await self._find_send_button()
        await send_btn.click()

        response_text = await self.get_response()

        current_url = self._page.url
        conv_id = current_url.split("/chat/")[-1].split("?")[0] if "/chat/" in current_url else None

        return ChatResponse(
            provider=self.name,
            message=response_text,
            conversation_id=conv_id,
            model="claude",
        )

    async def get_response(self) -> str:
        # Wait for generation to start (stop button appears)
        try:
            await self._page.wait_for_selector(
                'button[aria-label*="Stop"], button[data-testid="stop-button"]',
                timeout=20000,
            )
        except Exception:
            pass

        # Wait for generation to finish (stop button disappears)
        try:
            await self._page.wait_for_selector(
                'button[aria-label*="Stop"], button[data-testid="stop-button"]',
                state="hidden",
                timeout=120000,
            )
        except Exception:
            pass

        await asyncio.sleep(0.5)

        for sel in RESPONSE_SELECTORS:
            messages = await self._page.locator(sel).all()
            if messages:
                return (await messages[-1].inner_text()).strip()
        return ""

    async def check_session(self) -> bool:
        try:
            await self._ensure_browser()
            # Don't navigate again if we're already on claude.ai
            if "claude.ai" not in self._page.url:
                await self._page.goto(BASE_URL, wait_until="domcontentloaded")
                await asyncio.sleep(1)
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
