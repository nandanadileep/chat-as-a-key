import asyncio
import logging
import re
import time
from typing import Optional
from urllib.parse import urlparse

from playwright.async_api import Locator, Page

from core.traced_send import send_with_optional_failure_trace

from .base import ChatResponse
from .playwright_bases import StealthChromePlaywrightProvider

logger = logging.getLogger(__name__)

BASE_URL = "https://chatgpt.com"

# Visible input is often a contenteditable shell; plain textarea / data-testid nodes may exist hidden.
_COMPOSER_CANDIDATES = (
    'div#prompt-textarea[contenteditable="true"]',
    'div[contenteditable="true"][data-testid="prompt-textarea"]',
    "#prompt-textarea",
    "div#prompt-textarea",
    'div.ProseMirror[contenteditable="true"]',
    'footer div[contenteditable="true"][role="textbox"]',
    'main div[contenteditable="true"][role="textbox"]',
    'div[contenteditable="true"][role="textbox"]',
    "textarea#prompt-textarea",
    '[data-testid="composer-input"]',
    'div[data-testid="composer"] textarea',
    "main form textarea",
    "footer textarea",
    '[data-testid="prompt-textarea"]',
    "form textarea",
    "textarea[data-id]",
    "textarea[tabindex='0']",
)

_COMPOSER_WAIT_CAP_MS = 25_000


def _ms_budget(end: float, cap: int = _COMPOSER_WAIT_CAP_MS) -> int:
    ms = int((end - time.monotonic()) * 1000)
    return max(400, min(cap, ms))


async def _try_locator_visible_composer(loc: Locator, *, end: float, last_err_holder: list) -> Optional[Locator]:
    cap = _COMPOSER_WAIT_CAP_MS
    bw = _ms_budget(end, cap)
    if bw < 800:
        return None
    try:
        attach_ms = min(bw, 20_000)
        await loc.wait_for(state="attached", timeout=attach_ms)
        try:
            await loc.scroll_into_view_if_needed(timeout=min(8_000, _ms_budget(end, cap)))
        except Exception:
            pass
        await loc.wait_for(state="visible", timeout=_ms_budget(end, cap))
        return loc
    except Exception as e:
        last_err_holder[0] = e
        return None


async def _wait_visible_composer(page: Page, timeout_ms: int = 120_000) -> Locator:
    end = time.monotonic() + timeout_ms / 1000.0
    last_err: Optional[Exception] = None
    last_holder: list = [None]

    while time.monotonic() < end:
        if _ms_budget(end) < 800:
            break
        role = page.get_by_role("textbox", name=re.compile(r"message|chat", re.I)).first
        hit = await _try_locator_visible_composer(role, end=end, last_err_holder=last_holder)
        if hit is not None:
            return hit
        if last_holder[0] is not None:
            last_err = last_holder[0]

        for sel in _COMPOSER_CANDIDATES:
            if _ms_budget(end) < 800:
                break
            loc = page.locator(sel).first
            hit = await _try_locator_visible_composer(loc, end=end, last_err_holder=last_holder)
            if hit is not None:
                return hit
            if last_holder[0] is not None:
                last_err = last_holder[0]

        if _ms_budget(end) >= 800:
            ph = page.get_by_placeholder(
                re.compile(r"message|ask|type|send|search|chat|prompt", re.I)
            ).first
            hit = await _try_locator_visible_composer(ph, end=end, last_err_holder=last_holder)
            if hit is not None:
                return hit
            if last_holder[0] is not None:
                last_err = last_holder[0]

        await asyncio.sleep(0.35)
    raise TimeoutError(f"ChatGPT composer not visible: {last_err}")


async def _chatgpt_explicitly_logged_out(page: Page) -> bool:
    u = page.url.lower()
    netloc = urlparse(u).netloc.lower()
    if "/auth/login" in u or "/log-in" in u:
        return True
    if netloc in ("auth.openai.com", "accounts.google.com"):
        return True
    if netloc.endswith(".auth.openai.com"):
        return True
    return False


def _chatgpt_on_app_surface(url: str) -> bool:
    n = urlparse(url).netloc.lower()
    return n == "chatgpt.com" or n.endswith(".chatgpt.com") or n == "chat.openai.com" or n.endswith(".chat.openai.com")


class ChatGPTProvider(StealthChromePlaywrightProvider):
    name = "chatgpt"
    BASE_URL = BASE_URL
    HEADED_ENV_VAR = "CHATGPT_HEADED"

    async def send_message(self, message: str, conversation_id: Optional[str] = None) -> ChatResponse:
        await self._ensure_browser()
        page = self._page

        async def _attempt() -> ChatResponse:
            url = f"{BASE_URL}/c/{conversation_id}" if conversation_id else BASE_URL
            await page.goto(url, wait_until="domcontentloaded")
            try:
                await page.wait_for_load_state("load", timeout=30_000)
            except Exception:
                pass
            try:
                await page.wait_for_load_state("networkidle", timeout=25_000)
            except Exception:
                pass
            await asyncio.sleep(3.0)

            for pattern in (
                r"Accept all",
                r"^Accept$",
                r"Continue",
                r"Dismiss",
                r"Not now",
                r"Maybe later",
                r"Skip",
                r"Got it",
                r"No thanks",
                r"Remind me later",
            ):
                try:
                    b = page.get_by_role("button", name=re.compile(pattern, re.I)).first
                    if await b.is_visible(timeout=600):
                        await b.click(timeout=2000)
                        await asyncio.sleep(0.4)
                except Exception:
                    pass
            for _ in range(3):
                try:
                    await page.keyboard.press("Escape")
                    await asyncio.sleep(0.2)
                except Exception:
                    break

            composer = await _wait_visible_composer(page)
            await composer.click()
            await composer.fill(message)
            await asyncio.sleep(0.3)

            await page.keyboard.press("Enter")

            response_text = await self._get_response_on_page(page)

            current_url = page.url
            conv_id = current_url.split("/c/")[-1].split("?")[0] if "/c/" in current_url else None

            return ChatResponse(
                provider=self.name,
                message=response_text,
                conversation_id=conv_id,
                model="chatgpt",
            )

        return await send_with_optional_failure_trace(
            page=page,
            page_url_hint="chatgpt.com",
            send_impl=_attempt,
        )

    async def _get_response_on_page(self, page: Page) -> str:
        try:
            await page.wait_for_selector('[data-testid="stop-button"]', timeout=15000)
        except Exception:
            pass

        try:
            await page.wait_for_selector('[data-testid="stop-button"]', state="hidden", timeout=120000)
        except Exception:
            pass

        await asyncio.sleep(0.5)

        messages = await page.locator('[data-message-author-role="assistant"]').all()
        if messages:
            return (await messages[-1].inner_text()).strip()
        return ""

    async def check_session(self) -> bool:
        try:
            await self._ensure_browser()
            await self._page.goto(BASE_URL, wait_until="domcontentloaded")
            await asyncio.sleep(2.0)
            if await _chatgpt_explicitly_logged_out(self._page):
                return False
            try:
                await _wait_visible_composer(self._page, timeout_ms=15_000)
                return True
            except Exception:
                pass
            if await _chatgpt_explicitly_logged_out(self._page):
                return False
            if _chatgpt_on_app_surface(self._page.url):
                logger.info(
                    "ChatGPT: composer not visible within probe window; treating session as alive "
                    "(headless/slow UI — send_message will still wait full timeout)"
                )
                return True
            return False
        except Exception as e:
            logger.warning("ChatGPT session check failed: %s", e)
            return False
