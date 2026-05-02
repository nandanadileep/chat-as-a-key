import asyncio
import logging
import re
import time
from typing import Optional
from urllib.parse import urlparse

from playwright.async_api import Page

from browser.generic_network_collector import GenericProviderNetworkCollector
from core.traced_send import send_with_optional_failure_trace
from parsers.generic_parser import parse_generic_turn

from .base import ChatResponse, PlaywrightProviderBase

logger = logging.getLogger(__name__)

BASE_URL = "https://chatgpt.com"

COMPOSER_SELECTORS = (
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
)


def _chatgpt_network_url(url: str) -> bool:
    u = url.lower()
    if "chatgpt.com" not in u and "chat.openai.com" not in u and "openai.com" not in u:
        return False
    if any(
        x in u
        for x in (
            "conversation.json",
            "backend-api",
            "completions",
            "/backend/",
            "conversation",
            "chat_stream",
            "/api/",
        )
    ):
        return True
    return False


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


class ChatGPTProvider(PlaywrightProviderBase):
    name = "chatgpt"
    BASE_URL = BASE_URL
    USE_STEALTH = True
    HEADED_ENV_VAR = "CHATGPT_HEADED"

    async def _extract_dom_assistant_text(self, page: Page) -> str:
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
            if await self.snapshot_challenge_or_bot_wall(self._page):
                return False
            try:
                await self._wait_first_visible_composer(self._page, COMPOSER_SELECTORS, timeout_ms=12_000)
                return True
            except Exception:
                pass
            if await _chatgpt_explicitly_logged_out(self._page):
                return False
            if await self.snapshot_challenge_or_bot_wall(self._page):
                return False
            if _chatgpt_on_app_surface(self._page.url):
                logger.info(
                    "ChatGPT: composer not visible in probe; treating session as alive (slow UI / headless)"
                )
                return True
            return False
        except Exception as e:
            logger.warning("ChatGPT session check failed: %s", e)
            return False

    async def send_message(self, message: str, conversation_id: Optional[str] = None) -> ChatResponse:
        async with self._request_lock:
            await self._ensure_browser()
            trace_page = self._page

            async def _attempt() -> ChatResponse:
                page = trace_page
                url = f"{BASE_URL}/c/{conversation_id}" if conversation_id else BASE_URL
                collector = GenericProviderNetworkCollector(page, _chatgpt_network_url)
                collector.clear()
                collector.attach()
                started = time.perf_counter()
                try:
                    await page.goto(url, wait_until="domcontentloaded")
                    try:
                        await page.wait_for_load_state("load", timeout=30_000)
                    except Exception:
                        pass
                    try:
                        await page.wait_for_load_state("networkidle", timeout=25_000)
                    except Exception:
                        pass
                    await asyncio.sleep(2.0)
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
                    composer = await self._wait_first_visible_composer(page, COMPOSER_SELECTORS, timeout_ms=45_000)
                    await composer.click()
                    await composer.fill(message)
                    await asyncio.sleep(0.3)
                    await page.keyboard.press("Enter")
                    await asyncio.sleep(0.5)
                finally:
                    await collector.wait_for_pending(3.0)
                    collector.detach()

                records = collector.get_records()
                dom_text = await self._extract_dom_assistant_text(page)
                parsed = parse_generic_turn(
                    provider=self.name,
                    network_records=records,
                    dom_text=dom_text,
                    started_perf=started,
                )
                if parsed.status != "success" or not (parsed.text or "").strip():
                    raise RuntimeError(
                        f"parse_failed source={parsed.source} errors={parsed.errors!r} dom_len={len(dom_text)}"
                    )
                current_url = page.url
                conv_id = current_url.split("/c/")[-1].split("?")[0] if "/c/" in current_url else None
                return ChatResponse(
                    provider=self.name,
                    message=parsed.text.strip(),
                    conversation_id=conv_id,
                    model="chatgpt",
                )

            return await send_with_optional_failure_trace(
                page=trace_page,
                page_url_hint="chatgpt.com",
                send_impl=_attempt,
            )
