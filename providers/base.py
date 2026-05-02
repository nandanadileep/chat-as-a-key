from __future__ import annotations

import asyncio
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from playwright.async_api import Browser, BrowserContext, Locator, Page, Playwright, async_playwright

from browser.headless_chrome import launch_provider_browser, new_stealth_context
from browser.playwright_cleanup import dispose_playwright_stack
from browser.tracing_launch import chromium_tracing_args

logger = logging.getLogger(__name__)


@dataclass
class ChatResponse:
    provider: str
    message: str
    conversation_id: Optional[str] = None
    model: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class ProviderStatus:
    provider: str
    active: bool
    session_live: bool
    error: Optional[str] = None
    last_checked: float = field(default_factory=time.time)


class BaseProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def login(self) -> bool:
        """Authenticate and establish a browser session."""
        ...

    @abstractmethod
    async def send_message(self, message: str, conversation_id: Optional[str] = None) -> ChatResponse:
        """Send a message and return the response."""
        ...

    @abstractmethod
    async def get_response(self) -> str:
        """Wait for and extract the LLM's response from the page."""
        ...

    @abstractmethod
    async def check_session(self) -> bool:
        """Return True if the current session is still valid."""
        ...

    @abstractmethod
    async def reset_session(self) -> bool:
        """Clear and re-establish the session."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Clean up browser resources."""
        ...


class PlaywrightProviderBase(BaseProvider, ABC):
    """Shared Playwright lifecycle for ChatGPT, Gemini, Grok, Perplexity, Copilot.

    - Browser: Google Chrome channel with Chromium fallback (``launch_provider_browser``).
    - ``HEADLESS`` env (default ``true``); optional ``HEADED_ENV_VAR`` on subclass forces headed window.
    - Per-instance ``asyncio.Lock`` — subclasses should wrap ``send_message`` body in ``async with self._request_lock:``.
    """

    name: str = ""
    BASE_URL: str = ""
    USE_STEALTH: bool = False
    """When True, use ``new_stealth_context`` (ChatGPT, Perplexity)."""
    HEADED_ENV_VAR: str = ""
    """If set, truthy env value disables headless (e.g. ``CHATGPT_HEADED``)."""

    def __init__(
        self,
        cookies: Optional[list] = None,
        storage_state: Optional[str] = None,
        *,
        headless: Optional[bool] = None,
        **_: object,
    ) -> None:
        self._cookies = cookies or []
        self._storage_state = storage_state
        if headless is not None:
            self._headless_default = bool(headless)
        else:
            self._headless_default = os.getenv("HEADLESS", "true").lower() in ("1", "true", "yes")
        self._request_lock = asyncio.Lock()
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    def _effective_headless(self) -> bool:
        if self.HEADED_ENV_VAR and os.getenv(self.HEADED_ENV_VAR, "").lower() in ("1", "true", "yes"):
            return False
        return self._headless_default

    async def _ensure_browser(self) -> None:
        hl = self._effective_headless()
        if self._browser is None:
            self._playwright = await async_playwright().start()
            try:
                self._browser = await launch_provider_browser(self._playwright, headless=hl)
            except Exception as e:
                logger.warning("PlaywrightProviderBase: channel=chrome failed (%s); using Chromium", e)
                self._browser = await self._playwright.chromium.launch(
                    headless=hl,
                    args=["--no-sandbox", "--disable-dev-shm-usage", *chromium_tracing_args()],
                )
        if self._context is None:
            if self.USE_STEALTH:
                self._context = await new_stealth_context(
                    self._browser,
                    storage_state=self._storage_state,
                    cookies=self._cookies or None,
                )
            else:
                opts: dict[str, Any] = {}
                if self._storage_state:
                    opts["storage_state"] = self._storage_state
                self._context = await self._browser.new_context(**opts)
                if self._cookies:
                    try:
                        await self._context.add_cookies(self._cookies)
                    except Exception as e:
                        logger.warning("add_cookies failed (non-fatal): %s", e)
        if self._page is None or self._page.is_closed():
            self._page = await self._context.new_page()

    async def login(self) -> bool:
        await self._ensure_browser()
        await self._page.goto(self.BASE_URL, wait_until="domcontentloaded")
        return await self.check_session()

    async def get_response(self) -> str:
        await self._ensure_browser()
        return await self._extract_dom_assistant_text(self._page)

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

    @staticmethod
    async def _wait_first_visible_composer(page: Page, selectors: Sequence[str], timeout_ms: int = 8_000) -> Locator:
        """Try selectors in order (same pattern as ClaudeOrchestrator._find_composer)."""
        last_err: Optional[Exception] = None
        for sel in selectors:
            loc = page.locator(sel).first
            try:
                await loc.wait_for(state="visible", timeout=timeout_ms)
                return loc
            except Exception as e:
                last_err = e
                continue
        raise RuntimeError(f"Composer not found. Tried: {selectors!r} last={last_err!r}")

    @staticmethod
    async def snapshot_challenge_or_bot_wall(page: Page) -> bool:
        """Human gates / Cloudflare-style interstitials (no reliable composer)."""
        try:
            u = page.url.lower()
            t = (await page.title()).lower()
            html = (await page.content()).lower()
        except Exception:
            return False
        if any(
            x in html
            for x in (
                "cf-turnstile",
                "verify you are human",
                "checking your browser",
                "just a moment",
            )
        ):
            return True
        if "challenge" in u or "cf-browser-verification" in u:
            return True
        if "turnstile" in t:
            return True
        return False

    @abstractmethod
    async def send_message(self, message: str, conversation_id: Optional[str] = None) -> ChatResponse:
        ...

    @abstractmethod
    async def check_session(self) -> bool:
        ...

    @abstractmethod
    async def _extract_dom_assistant_text(self, page: Page) -> str:
        """Provider-specific DOM fallback after network parse."""
        ...
