import asyncio
import logging
import os
import time
from typing import TYPE_CHECKING, Optional
from urllib.parse import urlparse

from browser.session_manager import BrowserSessionManager

from .base import BaseProvider, ChatResponse

if TYPE_CHECKING:
    from core.orchestrator import ClaudeOrchestrator

logger = logging.getLogger(__name__)

BASE_URL = "https://claude.ai"
NEW_CHAT_URL = f"{BASE_URL}/new"
DEFAULT_PROFILE_DIR = os.path.join("sessions", "claude_profile")

# Positive signals (logged in) — same family as orchestrator composer selectors
_LOGGED_IN_SELECTORS = [
    'div.ProseMirror[contenteditable="true"]',
    '[data-testid="chat-input"] [contenteditable="true"]',
    'textarea[placeholder*="Message"]',
    '[data-testid="chat-input"]',
    '[data-testid="chat-input"] textarea',
    "div[contenteditable='true'][role='textbox']",
]

# Max time to wait for /new (or composer) after navigation — SPAs often need >10s.
_SESSION_WAIT_SEC = 35


class ClaudeProvider(BaseProvider):
    name = "claude"

    def __init__(self, cookies: Optional[list] = None, storage_state: Optional[str] = None):
        self._cookies = cookies or []
        self._session = BrowserSessionManager(
            storage_state_or_profile=storage_state,
            cookies=cookies,
            default_profile_dir=DEFAULT_PROFILE_DIR,
            headless=False,
        )
        self._orch: Optional["ClaudeOrchestrator"] = None

    def _get_orch(self) -> "ClaudeOrchestrator":
        if self._orch is None:
            from core.orchestrator import ClaudeOrchestrator

            self._orch = ClaudeOrchestrator(self._session)
        return self._orch

    async def _ensure_browser(self) -> None:
        await self._session.ensure_browser()

    async def login(self) -> bool:
        await self._session.ensure_browser()
        page = self._session.page
        if page is None:
            return False
        await page.goto(NEW_CHAT_URL, wait_until="domcontentloaded")
        ok = await self._wait_for_claude_ready(page, _SESSION_WAIT_SEC)
        if ok:
            logger.info("Claude login probe succeeded")
        else:
            logger.warning("Claude login probe failed (see prior session logs)")
        return ok

    async def send_message(self, message: str, conversation_id: Optional[str] = None) -> ChatResponse:
        return await self._get_orch().send_message(
            message,
            conversation_id,
            check_session_cb=self.check_session,
            reset_session_cb=self.reset_session,
        )

    async def get_response(self) -> str:
        await self._ensure_browser()
        page = self._session.page
        if page is None:
            return ""
        for sel in (
            '[data-testid="assistant-message"]',
            ".font-claude-message",
            '[class*="AssistantMessage"]',
        ):
            messages = await page.locator(sel).all()
            if messages:
                return (await messages[-1].inner_text()).strip()
        return ""

    async def _snapshot_cloudflare_or_challenge(self, page) -> bool:
        title = (await page.title()).lower()
        url = page.url.lower()
        return (
            "just a moment" in title
            or "challenge" in url
            or "cf-browser-verification" in url
            or "turnstile" in title
        )

    async def _snapshot_explicitly_logged_out(self, page) -> bool:
        url = page.url.lower()
        path = urlparse(page.url).path.lower()
        if path.startswith("/login"):
            return True
        base = url.split("?", 1)[0]
        if "claude.ai" in url and "/login" in base:
            return True
        return False

    async def _snapshot_logged_in(self, page) -> bool:
        if await self._snapshot_cloudflare_or_challenge(page):
            return False
        path = urlparse(page.url).path.lower()
        if path == "/new" or path.startswith("/chat/"):
            return True
        for sel in _LOGGED_IN_SELECTORS:
            loc = page.locator(sel).first
            try:
                if await loc.is_visible(timeout=800):
                    return True
            except Exception:
                continue
        return False

    async def _wait_for_claude_ready(self, page, timeout_sec: float) -> bool:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            try:
                if await self._snapshot_cloudflare_or_challenge(page):
                    logger.warning("Claude session: Cloudflare/challenge page — complete in a real browser, then retry")
                    return False
                if await self._snapshot_explicitly_logged_out(page):
                    return False
                if await self._snapshot_logged_in(page):
                    return True
            except Exception as e:
                logger.debug("session probe tick: %s", e)
            await asyncio.sleep(1)
        try:
            logger.warning(
                "Claude session probe timed out after %ss — last url=%s path=%s",
                int(timeout_sec),
                page.url[:120],
                urlparse(page.url).path,
            )
        except Exception:
            pass
        return False

    async def check_session(self) -> bool:
        try:
            page = await self._session.ensure_browser()
            await page.goto(NEW_CHAT_URL, wait_until="domcontentloaded")
            return await self._wait_for_claude_ready(page, _SESSION_WAIT_SEC)
        except Exception as e:
            logger.warning("Claude session check failed: %s", e)
            return False

    async def reset_session(self) -> bool:
        await self.close()
        return await self.login()

    async def close(self) -> None:
        await self._session.close()
