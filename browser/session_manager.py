from __future__ import annotations

import logging
import os
from enum import Enum
from typing import Optional

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from browser.tracing_launch import chromium_tracing_args

logger = logging.getLogger(__name__)

# Chrome refuses a second process on the same user-data-dir (ProcessSingleton / SingletonLock).
_SINGLETON_FILES = ("SingletonLock", "SingletonCookie", "SingletonSocket")


def _clear_stale_profile_singleton_files(user_data_dir: str) -> None:
    """Remove Chrome singleton lock files after a crash or if no browser holds the profile.

    If another Chrome is still running with this profile, removal may fail — quit that Chrome first.
    """
    for name in _SINGLETON_FILES:
        path = os.path.join(user_data_dir, name)
        if not os.path.exists(path):
            continue
        try:
            os.remove(path)
            logger.warning("Removed stale profile file: %s", path)
        except OSError as e:
            logger.warning("Could not remove %s (%s). Quit any Chrome using this profile.", path, e)


class SessionMode(str, Enum):
    PERSISTENT_PROFILE = "persistent_profile"
    STORAGE_STATE = "storage_state"


class BrowserSessionManager:
    """Persistent profile (directory) OR Playwright storage_state JSON — never pass JSON as user_data_dir."""

    def __init__(
        self,
        storage_state_or_profile: Optional[str] = None,
        cookies: Optional[list] = None,
        *,
        default_profile_dir: str = os.path.join("sessions", "claude_profile"),
        headless: bool = False,
    ) -> None:
        self._cookies = cookies or []
        self._default_profile_dir = default_profile_dir
        self._raw = storage_state_or_profile or default_profile_dir
        self._resolved = os.path.abspath(self._raw)
        self._headless = headless
        self._mode: Optional[SessionMode] = None
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    @property
    def mode(self) -> Optional[SessionMode]:
        return self._mode

    @property
    def page(self) -> Optional[Page]:
        return self._page

    @property
    def context(self) -> Optional[BrowserContext]:
        return self._context

    def _classify_path(self) -> tuple[SessionMode, str]:
        path = self._resolved
        if os.path.isdir(path):
            logger.info("Claude session: using persistent profile at %s", path)
            return SessionMode.PERSISTENT_PROFILE, path
        if os.path.isfile(path) and path.lower().endswith(".json"):
            logger.info("Claude session: using storage_state JSON at %s", path)
            return SessionMode.STORAGE_STATE, path
        if path.lower().endswith(".json"):
            raise FileNotFoundError(
                f"Storage state JSON not found: {path}. Export a Playwright storage state file or use a profile directory."
            )
        os.makedirs(path, exist_ok=True)
        logger.info("Claude session: using persistent profile at %s (directory ensured)", path)
        return SessionMode.PERSISTENT_PROFILE, path

    async def ensure_browser(self) -> Page:
        if self._context is not None and self._page is not None and not self._page.is_closed():
            return self._page

        mode, resolved = self._classify_path()
        self._mode = mode
        self._playwright = await async_playwright().start()

        common_args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--window-position=-32000,-32000",
        ]
        common_args.extend(chromium_tracing_args())
        viewport = {"width": 1280, "height": 800}

        if mode == SessionMode.PERSISTENT_PROFILE:
            launch_kwargs = dict(
                user_data_dir=resolved,
                headless=self._headless,
                args=common_args,
                viewport=viewport,
                no_viewport=False,
            )
            for singleton_retry in range(2):
                try:
                    try:
                        self._context = await self._playwright.chromium.launch_persistent_context(
                            channel="chrome",
                            **launch_kwargs,
                        )
                    except Exception as e:
                        logger.error(
                            "launch_persistent_context(channel=chrome) failed: %s. "
                            "Install Google Chrome; a directory profile from desktop Chrome is not "
                            "supported with bundled Chromium (cookies/session differ).",
                            e,
                        )
                        raise
                    break
                except Exception as e:
                    msg = str(e)
                    if singleton_retry == 0 and any(
                        s in msg
                        for s in (
                            "ProcessSingleton",
                            "SingletonLock",
                            "profile is already in use",
                            "profile directory",
                        )
                    ):
                        logger.warning(
                            "Chrome profile lock — if you opened Chrome with "
                            "--user-data-dir for this folder, quit it fully (Cmd+Q). "
                            "Retrying after clearing stale singleton files once."
                        )
                        _clear_stale_profile_singleton_files(resolved)
                        continue
                    raise
            pages = self._context.pages
            self._page = pages[0] if pages else await self._context.new_page()
        else:
            try:
                self._browser = await self._playwright.chromium.launch(
                    channel="chrome",
                    headless=self._headless,
                    args=common_args,
                )
            except Exception as e:
                logger.warning("Chrome channel failed (%s); using bundled Chromium", e)
                self._browser = await self._playwright.chromium.launch(
                    headless=self._headless,
                    args=common_args,
                )
            self._context = await self._browser.new_context(
                viewport=viewport,
                storage_state=resolved,
            )
            self._page = await self._context.new_page()

        if self._cookies:
            try:
                await self._context.add_cookies(self._cookies)
            except Exception as e:
                logger.warning("add_cookies failed (non-fatal): %s", e)

        self._page.set_default_navigation_timeout(90_000)
        return self._page

    async def close(self) -> None:
        if self._page and not self._page.is_closed():
            try:
                await self._page.close()
            except Exception as e:
                logger.debug("page.close: %s", e)
        self._page = None

        if self._context:
            try:
                await self._context.close()
            except Exception as e:
                logger.debug("context.close: %s", e)
        self._context = None

        if self._browser:
            try:
                await self._browser.close()
            except Exception as e:
                logger.debug("browser.close: %s", e)
        self._browser = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as e:
                logger.debug("playwright.stop: %s", e)
        self._playwright = None
        self._mode = None
