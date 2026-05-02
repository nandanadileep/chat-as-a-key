"""Shared Playwright lifecycle for providers that use the same browser/context pattern."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Optional

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from browser.headless_chrome import launch_provider_browser, new_stealth_context
from browser.playwright_cleanup import dispose_playwright_stack
from browser.tracing_launch import chromium_tracing_args

from .base import BaseProvider, ChatResponse


class ChromiumTracingPlaywrightProvider(BaseProvider, ABC):
    """Bundled Chromium + tracing args + optional storage_state (Gemini, Grok, Copilot)."""

    name: str = ""
    BASE_URL: str = ""

    def __init__(
        self,
        cookies: Optional[list] = None,
        storage_state: Optional[str] = None,
        *,
        headless: bool = True,
        **_: object,
    ) -> None:
        self._cookies = cookies or []
        self._storage_state = storage_state
        self._headless = headless
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    async def _ensure_browser(self) -> None:
        if self._browser is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self._headless,
                args=["--no-sandbox", "--disable-dev-shm-usage", *chromium_tracing_args()],
            )
        if self._context is None:
            context_opts: dict = {}
            if self._storage_state:
                context_opts["storage_state"] = self._storage_state
            self._context = await self._browser.new_context(**context_opts)
            if self._cookies:
                await self._context.add_cookies(self._cookies)
        if self._page is None or self._page.is_closed():
            self._page = await self._context.new_page()

    @abstractmethod
    async def send_message(self, message: str, conversation_id: Optional[str] = None) -> ChatResponse: ...

    @abstractmethod
    async def check_session(self) -> bool: ...

    @abstractmethod
    async def _get_response_on_page(self, page: Page) -> str: ...

    async def login(self) -> bool:
        await self._ensure_browser()
        await self._page.goto(self.BASE_URL, wait_until="domcontentloaded")
        return await self.check_session()

    async def get_response(self) -> str:
        await self._ensure_browser()
        return await self._get_response_on_page(self._page)

    async def reset_session(self) -> bool:
        await self.close()
        self._context = None
        self._page = None
        return await self.login()

    async def close(self) -> None:
        await dispose_playwright_stack(
            page=self._page,
            context=self._context,
            browser=self._browser,
            playwright=self._playwright,
        )
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None


class StealthChromePlaywrightProvider(BaseProvider, ABC):
    """Google Chrome channel + stealth context (ChatGPT, Perplexity)."""

    name: str = ""
    BASE_URL: str = ""
    HEADED_ENV_VAR: str = "CHATGPT_HEADED"

    def __init__(self, cookies: Optional[list] = None, storage_state: Optional[str] = None, **_: object) -> None:
        self._cookies = cookies or []
        self._storage_state = storage_state
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    def _headed(self) -> bool:
        return os.getenv(self.HEADED_ENV_VAR, "").lower() in ("1", "true", "yes")

    async def _ensure_browser(self) -> None:
        if self._browser is None:
            self._playwright = await async_playwright().start()
            self._browser = await launch_provider_browser(self._playwright, headless=not self._headed())
        if self._context is None:
            self._context = await new_stealth_context(
                self._browser,
                storage_state=self._storage_state,
                cookies=self._cookies or None,
            )
        if self._page is None or self._page.is_closed():
            self._page = await self._context.new_page()

    @abstractmethod
    async def send_message(self, message: str, conversation_id: Optional[str] = None) -> ChatResponse: ...

    @abstractmethod
    async def check_session(self) -> bool: ...

    @abstractmethod
    async def _get_response_on_page(self, page: Page) -> str: ...

    async def login(self) -> bool:
        await self._ensure_browser()
        await self._page.goto(self.BASE_URL, wait_until="domcontentloaded")
        return await self.check_session()

    async def get_response(self) -> str:
        await self._ensure_browser()
        return await self._get_response_on_page(self._page)

    async def reset_session(self) -> bool:
        await self.close()
        self._context = None
        self._page = None
        return await self.login()

    async def close(self) -> None:
        await dispose_playwright_stack(
            page=self._page,
            context=self._context,
            browser=self._browser,
            playwright=self._playwright,
        )
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
