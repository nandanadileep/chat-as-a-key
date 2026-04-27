import asyncio
import logging
import os
from typing import Optional

from playwright.async_api import async_playwright, BrowserContext, Page, Playwright

from .base import BaseProvider, ChatResponse

logger = logging.getLogger(__name__)

BASE_URL = "https://claude.ai"
NEW_CHAT_URL = f"{BASE_URL}/new"
DEFAULT_PROFILE_DIR = os.path.join("sessions", "claude_profile")

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
        # cookies/storage_state kept for API compat but profile dir is the real mechanism
        self._profile_dir = storage_state or DEFAULT_PROFILE_DIR
        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    async def _ensure_browser(self) -> None:
        if self._context is not None and not self._page.is_closed():
            return

        os.makedirs(self._profile_dir, exist_ok=True)
        self._playwright = await async_playwright().start()

        launch_kwargs = dict(
            user_data_dir=self._profile_dir,
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--window-position=-32000,-32000",  # off-screen, invisible
            ],
            viewport={"width": 1280, "height": 800},
            no_viewport=False,
        )

        try:
            self._context = await self._playwright.chromium.launch_persistent_context(
                channel="chrome", **launch_kwargs
            )
        except Exception:
            self._context = await self._playwright.chromium.launch_persistent_context(
                **launch_kwargs
            )

        pages = self._context.pages
        self._page = pages[0] if pages else await self._context.new_page()
        self._page.set_default_navigation_timeout(60000)

    async def _find_composer(self) -> object:
        for sel in COMPOSER_SELECTORS:
            loc = self._page.locator(sel).first
            try:
                await loc.wait_for(state="visible", timeout=5000)
                return loc
            except Exception:
                continue
        raise RuntimeError(f"Composer not found. Tried: {COMPOSER_SELECTORS}")

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
        await asyncio.sleep(3)
        return await self.check_session()

    async def send_message(self, message: str, conversation_id: Optional[str] = None) -> ChatResponse:
        await self._ensure_browser()

        url = f"{BASE_URL}/chat/{conversation_id}" if conversation_id else NEW_CHAT_URL
        await self._page.goto(url, wait_until="domcontentloaded")
        await asyncio.sleep(3)

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
        try:
            await self._page.wait_for_selector(
                'button[aria-label*="Stop"], button[data-testid="stop-button"]',
                timeout=20000,
            )
        except Exception:
            pass
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
            title = await self._page.title()
            url = self._page.url
            if "just a moment" in title.lower() or "challenge" in url:
                logger.warning("Claude blocked by Cloudflare — run capture script again")
                return False
            login_visible = await self._page.locator('text="Log in"').count() > 0
            return not login_visible
        except Exception as e:
            logger.warning("Claude session check failed: %s", e)
            return False

    async def reset_session(self) -> bool:
        await self.close()
        return await self.login()

    async def close(self) -> None:
        if self._page and not self._page.is_closed():
            await self._page.close()
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()
        self._page = None
        self._context = None
        self._playwright = None
