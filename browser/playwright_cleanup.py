"""Best-effort teardown when the Playwright driver dies (e.g. SIGINT during startup)."""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def dispose_playwright_stack(
    *,
    page: Optional[object] = None,
    context: Optional[object] = None,
    browser: Optional[object] = None,
    playwright: Optional[object] = None,
) -> None:
    """Close page → context → browser → playwright; swallow driver disconnect errors."""
    if page is not None:
        try:
            if not page.is_closed():
                await page.close()
        except Exception as e:
            logger.debug("playwright page.close: %s", e)
    if context is not None:
        try:
            await context.close()
        except Exception as e:
            logger.debug("playwright context.close: %s", e)
    if browser is not None:
        try:
            await browser.close()
        except Exception as e:
            logger.debug("playwright browser.close: %s", e)
    if playwright is not None:
        try:
            await playwright.stop()
        except Exception as e:
            logger.debug("playwright.stop: %s", e)
