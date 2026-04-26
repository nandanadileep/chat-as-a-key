"""
Run this once per provider to save a full browser session (storage state).
Opens a real browser window so you can log in manually and pass any bot
challenges. The saved state is reused by the server for all requests.

Usage:
    python scripts/capture_session.py claude
    python scripts/capture_session.py chatgpt
    ...

After running, set in .env:
    CLAUDE_ENABLED=true
    CLAUDE_STORAGE_STATE=sessions/claude.json
"""

import asyncio
import os
import sys

from playwright.async_api import async_playwright

PROVIDER_URLS = {
    "claude":      "https://claude.ai",
    "chatgpt":     "https://chatgpt.com",
    "gemini":      "https://gemini.google.com",
    "grok":        "https://grok.com",
    "perplexity":  "https://www.perplexity.ai",
    "copilot":     "https://copilot.microsoft.com",
}

# Cookie that confirms a successful login for each provider
SESSION_COOKIE = {
    "claude":      "sessionKey",
    "chatgpt":     "__Secure-next-auth.session-token",
    "gemini":      "SID",
    "grok":        "auth_token",
    "perplexity":  "__Secure-next-auth.session-token",
    "copilot":     "MUID",
}


async def capture(provider: str) -> None:
    if provider not in PROVIDER_URLS:
        print(f"Unknown provider '{provider}'. Choose from: {', '.join(PROVIDER_URLS)}")
        sys.exit(1)

    url = PROVIDER_URLS[provider]
    session_cookie = SESSION_COOKIE.get(provider)
    out_path = os.path.join("sessions", f"{provider}.json")
    os.makedirs("sessions", exist_ok=True)

    print(f"\nOpening {url} ...")
    print("Log in completely (solve any CAPTCHA, finish the login flow).")
    print("The script will detect when you are logged in and save automatically.\n")

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(channel="chrome", headless=False, args=["--no-sandbox"])
        except Exception:
            browser = await p.chromium.launch(headless=False, args=["--no-sandbox"])

        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()
        await page.goto(url)

        print("Waiting for you to log in", end="", flush=True)
        logged_in = False
        for _ in range(300):  # wait up to 5 minutes
            await asyncio.sleep(1)
            print(".", end="", flush=True)
            cookies = await context.cookies()
            cookie_names = {c["name"] for c in cookies}
            if session_cookie and session_cookie in cookie_names:
                logged_in = True
                break
            # Fallback: check URL / title
            title = await page.title()
            current_url = page.url
            if "just a moment" not in title.lower() and "login" not in current_url.lower():
                if provider == "claude" and "claude.ai" in current_url and "/new" not in current_url and "challenge" not in current_url:
                    # Extra wait to make sure cookies are all set
                    await asyncio.sleep(3)
                    logged_in = True
                    break

        print()
        if not logged_in:
            print("Warning: could not confirm login — saving state anyway.")

        await context.storage_state(path=out_path)
        cookie_count = len(await context.cookies())
        await browser.close()

    print(f"\nSaved {cookie_count} cookies to {out_path}")
    print(f"\nAdd to your .env:")
    print(f"  {provider.upper()}_ENABLED=true")
    print(f"  {provider.upper()}_STORAGE_STATE={out_path}")
    print(f"\nThen restart: python3 server.py")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    asyncio.run(capture(sys.argv[1].lower()))
