"""
Run this once to log into a provider through a real Chrome window.

Usage:
    python scripts/capture_session.py claude
    python scripts/capture_session.py chatgpt
    ...

After running, set in .env:
  - Claude: persistent profile dir (server uses Chrome + folder).
  - ChatGPT, Gemini, Grok, Perplexity, Copilot: Playwright ``storage_state`` JSON
    (exported here) — point ``*_STORAGE_STATE`` at ``sessions/<provider>.json``.
"""

import asyncio
import os
import sys

from playwright.async_api import async_playwright

PROVIDER_URLS = {
    "claude":     "https://claude.ai",
    "chatgpt":    "https://chatgpt.com",
    "gemini":     "https://gemini.google.com",
    "grok":       "https://grok.com",
    "perplexity": "https://www.perplexity.ai",
    "copilot":    "https://copilot.microsoft.com",
}

LOGGED_IN_CHECK = {
    "claude":     lambda url, title: "claude.ai" in url and "login" not in url and "just a moment" not in title.lower(),
    "chatgpt":    lambda url, title: "chatgpt.com" in url and "auth" not in url,
    "gemini":     lambda url, title: "gemini.google.com" in url and "accounts.google" not in url,
    "grok":       lambda url, title: "grok.com" in url and "login" not in url,
    "perplexity": lambda url, title: "perplexity.ai" in url and "login" not in url,
    "copilot":    lambda url, title: "copilot.microsoft.com" in url and "login" not in url,
}


async def capture(provider: str) -> None:
    if provider not in PROVIDER_URLS:
        print(f"Unknown provider. Choose from: {', '.join(PROVIDER_URLS)}")
        sys.exit(1)

    url = PROVIDER_URLS[provider]
    profile_dir = os.path.join("sessions", f"{provider}_profile")
    os.makedirs(profile_dir, exist_ok=True)
    is_logged_in = LOGGED_IN_CHECK[provider]

    print(f"\nOpening {url} in Chrome...")
    print("Log in fully (solve any CAPTCHA, finish the login flow).")
    print("Chrome will close automatically once you are logged in.\n")

    async with async_playwright() as p:
        try:
            ctx = await p.chromium.launch_persistent_context(
                profile_dir,
                channel="chrome",
                headless=False,
                args=["--no-sandbox"],
                viewport={"width": 1280, "height": 800},
            )
        except Exception:
            ctx = await p.chromium.launch_persistent_context(
                profile_dir,
                headless=False,
                args=["--no-sandbox"],
                viewport={"width": 1280, "height": 800},
            )

        pages = ctx.pages
        page = pages[0] if pages else await ctx.new_page()
        await page.goto(url)

        print("Waiting for login", end="", flush=True)
        logged_ok = False
        for _ in range(300):
            await asyncio.sleep(1)
            print(".", end="", flush=True)
            try:
                current_url = page.url
                title = await page.title()
                if is_logged_in(current_url, title):
                    await asyncio.sleep(2)  # let cookies settle
                    logged_ok = True
                    break
            except Exception:
                pass

        if not logged_ok:
            print("\n\nTimed out waiting for login (5 min). Close and re-run when you can finish sign-in.")
            await ctx.close()
            sys.exit(1)

        print(f"\n\nLogged in! Chrome profile saved to: {profile_dir}")

        os.makedirs("sessions", exist_ok=True)
        json_path = os.path.join("sessions", f"{provider}.json")
        await ctx.storage_state(path=json_path)
        print(f"Playwright storage_state saved to: {json_path}")

        await ctx.close()

    print(f"\nAdd to your .env (if not already set):")
    print(f"  {provider.upper()}_ENABLED=true")
    if provider == "claude":
        print(f"  {provider.upper()}_STORAGE_STATE={profile_dir}")
    else:
        print(f"  {provider.upper()}_STORAGE_STATE={json_path}")
    print(f"\nThen restart: python3 server.py")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    asyncio.run(capture(sys.argv[1].lower()))
