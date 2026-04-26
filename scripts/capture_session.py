"""
Run this once per provider to save a full browser session (storage state).
It opens a real browser window so you can log in manually and pass any
bot challenges. The saved state is then used by the server.

Usage:
    python scripts/capture_session.py claude
    python scripts/capture_session.py chatgpt
    python scripts/capture_session.py gemini
    ...

After running, set in .env:
    CLAUDE_STORAGE_STATE=sessions/claude.json
    (and remove or leave empty CLAUDE_COOKIES)
"""

import asyncio
import os
import sys

from playwright.async_api import async_playwright

PROVIDER_URLS = {
    "claude": "https://claude.ai",
    "chatgpt": "https://chatgpt.com",
    "gemini": "https://gemini.google.com",
    "grok": "https://grok.com",
    "perplexity": "https://www.perplexity.ai",
    "copilot": "https://copilot.microsoft.com",
}


async def capture(provider: str) -> None:
    if provider not in PROVIDER_URLS:
        print(f"Unknown provider '{provider}'. Choose from: {', '.join(PROVIDER_URLS)}")
        sys.exit(1)

    url = PROVIDER_URLS[provider]
    out_path = os.path.join("sessions", f"{provider}.json")
    os.makedirs("sessions", exist_ok=True)

    print(f"\nOpening {url} in a real browser window.")
    print("Log in manually (complete any CAPTCHA/challenge), then come back here and press Enter.\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--no-sandbox"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()
        await page.goto(url)

        input("Press Enter once you are fully logged in...")

        await context.storage_state(path=out_path)
        await browser.close()

    print(f"\nSession saved to {out_path}")
    print(f"Add this to your .env:")
    print(f"  {provider.upper()}_ENABLED=true")
    print(f"  {provider.upper()}_STORAGE_STATE={out_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    asyncio.run(capture(sys.argv[1].lower()))
