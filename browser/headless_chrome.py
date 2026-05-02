"""Headless browser for provider UIs that reject stock Playwright Chromium."""

from __future__ import annotations

import logging
from typing import Any, Optional

from playwright.async_api import Browser, BrowserContext, Playwright

from browser.tracing_launch import chromium_tracing_args

logger = logging.getLogger(__name__)

# Match real desktop Chrome more closely than default Chromium fingerprint.
_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    *chromium_tracing_args(),
]


async def launch_provider_browser(playwright: Playwright, *, headless: bool = True) -> Browser:
    """Prefer Google Chrome (`channel='chrome'`); fall back to bundled Chromium."""
    args = list(_LAUNCH_ARGS)
    if not headless:
        # Keep the window mostly off-screen when using headed mode for automation.
        args.extend(["--window-position=-32000,-32000", "--window-size=1280,800"])
    try:
        browser = await playwright.chromium.launch(
            channel="chrome",
            headless=headless,
            args=args,
        )
        mode = "headless" if headless else "headed (window parked off-screen)"
        logger.info("Playwright: launched Google Chrome (channel=chrome, %s)", mode)
        return browser
    except Exception as e:
        logger.warning("Playwright: channel=chrome failed (%s); using Chromium", e)
        return await playwright.chromium.launch(headless=headless, args=args)


async def new_stealth_context(
    browser: Browser,
    *,
    storage_state: Optional[str] = None,
    cookies: Optional[list] = None,
    extra_context_options: Optional[dict[str, Any]] = None,
) -> BrowserContext:
    opts: dict[str, Any] = {
        "viewport": {"width": 1280, "height": 800},
        "locale": "en-US",
        "timezone_id": "America/Los_Angeles",
    }
    if storage_state:
        opts["storage_state"] = storage_state
    if extra_context_options:
        opts.update(extra_context_options)
    ctx = await browser.new_context(**opts)
    if cookies:
        try:
            await ctx.add_cookies(cookies)
        except Exception as e:
            logger.warning("add_cookies failed (non-fatal): %s", e)
    try:
        from playwright_stealth import Stealth

        await Stealth(
            navigator_platform_override="MacIntel",
            navigator_languages_override=("en-US", "en"),
        ).apply_stealth_async(ctx)
    except Exception as e:
        logger.warning("playwright-stealth apply failed (ignored): %s", e)
    return ctx
