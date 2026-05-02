# Setup guide

This walks you from zero to a working **chat-as-a-key** server. Read **[DISCLAIMER.md](DISCLAIMER.md)** first; you are responsible for how you use third-party chat services.

---

## 1. What you need

| Requirement | Notes |
|-------------|--------|
| **Python 3.12+** | For running without Docker. |
| **Docker + Docker Compose** | Optional; easiest runtime if you use **cookies** or a **`.json` storage state`** file (see Claude note below). |
| **Google Chrome** (desktop) | **Required on the host** if you use **Claude** with a **persistent profile folder** (`sessions/claude_profile`). The server launches Playwright with `channel="chrome"` for directory profiles. |
| **Disk & RAM** | Browser automation is heavy; allow ~1–2 GB RAM per active provider and enough disk for browser profiles under `sessions/`. |

**Claude + Docker:** The default `Dockerfile` installs **Playwright Chromium**, not Google Chrome. A **directory** profile (`CLAUDE_STORAGE_STATE=sessions/claude_profile`) **will not work** inside that image as shipped, because Claude’s persistent profile path **requires Chrome**. For Docker, use either:

- **`CLAUDE_COOKIES`** or **`CLAUDE_STORAGE_STATE`** pointing to a **`.json`** file you produced on a machine with Chrome, then mount `./sessions`, or  
- Run the **API on your Mac/Linux** where Chrome is installed, or  
- Extend the image to install Google Chrome (not covered here).

---

## 2. Clone the repository

```bash
git clone https://github.com/YOUR_GITHUB_USER/chat-as-a-key.git
cd chat-as-a-key
```

(Use your fork URL if you cloned elsewhere.)

---

## 3. Create a session for each provider you want

You must give the server a **logged-in session**: either **cookies**, a **Playwright `storage_state` JSON**, or a **Chrome user-data directory** (profile folder).

### Option A — `capture_session.py` (Chrome window, recommended)

Use the bundled script once per provider. It opens a real window; you log in manually.

```bash
pip install -r requirements.txt
playwright install chromium
python scripts/capture_session.py claude
```

**Claude:** the script saves a **Chrome profile directory** under `sessions/claude_profile`. Use that path in `.env`:

```dotenv
CLAUDE_ENABLED=true
CLAUDE_STORAGE_STATE=sessions/claude_profile
```

**ChatGPT, Gemini, Grok, Perplexity, Copilot:** the same script also writes **`sessions/<provider>.json`** (Playwright `storage_state`). The server expects that **JSON path** for these providers, not the `_profile` folder:

```dotenv
CHATGPT_ENABLED=true
CHATGPT_STORAGE_STATE=sessions/chatgpt.json
```

Repeat capture for `gemini`, `perplexity`, etc., then set the matching `*_ENABLED` / `*_STORAGE_STATE` lines the script prints.

**Rules:**

- **Do not** open the same **Claude** profile folder in a normal Chrome window while the server is using it (Chrome will complain about the profile being in use / `SingletonLock`).
- If the server crashed, you may see stale locks; quit Chrome using that profile, or remove `SingletonLock` / `SingletonCookie` / `SingletonSocket` inside the profile folder if no Chrome is running.

### Option B — Cookie JSON in `.env`

1. Log into the provider in your everyday browser.
2. Export cookies with an extension such as **Cookie-Editor** (export as JSON array).
3. Paste **one line** into `.env` (escape quotes if needed, or use a minimal set of cookies the provider actually needs — often session cookies).

Example shape (values are fake):

```dotenv
CLAUDE_ENABLED=true
CLAUDE_COOKIES=[{"name":"sessionKey","value":"…","domain":"claude.ai","path":"/","secure":true,"httpOnly":true}]
```

See **[README.md](README.md)** → *Configuration reference* for all provider prefixes.

### Option C — `storage_state` JSON (cookies + `localStorage`)

Useful when the site stores auth partly in `localStorage`. On a machine with Playwright:

```bash
python -c "
import asyncio
from playwright.async_api import async_playwright

async def capture():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, channel='chrome')
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto('https://claude.ai')
        input('Log in, then press Enter here...')
        await context.storage_state(path='sessions/claude.json')
        await browser.close()
        print('Saved sessions/claude.json')

asyncio.run(capture())
"
```

Then:

```dotenv
CLAUDE_ENABLED=true
CLAUDE_STORAGE_STATE=sessions/claude.json
```

**Path rules:** If `CLAUDE_STORAGE_STATE` is a **directory**, it is treated as a **persistent Chrome profile**. If it is a **file** ending in **`.json`**, it is treated as **Playwright storage state**.

---

## 4. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env`:

1. Set **`HOST`** / **`PORT`** if you need something other than `0.0.0.0:8000`.
2. For each provider you use: **`{PROVIDER}_ENABLED=true`** and either **`{PROVIDER}_COOKIES`** or **`{PROVIDER}_STORAGE_STATE`**.
3. Optional: **`ENABLE_TRACING=true`** — adds Chrome remote debugging and, on **failed** sends, writes artifacts under **`.o11y/{run_id}/`** (see README). Leave `false` unless you are debugging.

Never commit **`.env`** or **`sessions/`** contents with real secrets. They are for your machine only.

---

## 5. Run the server

### With Docker Compose

```bash
docker compose up --build
```

- Loads variables from **`.env`**.
- Mounts **`./sessions`** into the container at **`/app/sessions`** — keep `*_STORAGE_STATE` paths relative to that (e.g. `sessions/claude.json`).

Open **http://localhost:8000** (or the host port you mapped).

### Without Docker

```bash
pip install -r requirements.txt
playwright install chromium
python server.py
```

On **Linux headless servers**, providers that need a visible login flow are painful; prefer capturing sessions on a desktop and shipping **`storage_state` JSON** or cookies.

---

## 6. Verify it works

**Smoke-test every enabled provider** (server must be running; uses your `.env` flags — same machine, repo root):

```bash
pip install httpx python-dotenv   # if not already from requirements.txt
python scripts/smoke_test_providers.py
```

Optional:

- `python scripts/smoke_test_providers.py --list-only` — enabled names only (no HTTP).
- `python scripts/smoke_test_providers.py --catalog` — every supported `provider` key vs enabled/disabled in `.env` (no HTTP).
- `python scripts/smoke_test_providers.py --markdown` — after a full run, prints a Markdown results table (for README / blog).

`CHAT_AS_A_KEY_URL` / `--base-url` if the server is not on `http://127.0.0.1:8000`. Long sends: `--timeout 600`. Provider reliability notes: **[docs/PROVIDER_BROWSER_MATRIX.md](docs/PROVIDER_BROWSER_MATRIX.md)** (includes copy-paste pitfalls, port-in-use, Claude profile lock).

**If `python server.py` fails with “address already in use”:** another instance is bound to `PORT` (default 8000). Stop the old process or set `PORT=8001` in `.env` / the shell and point the smoke script at `--base-url http://127.0.0.1:8001`.

**Health:**

```bash
curl -s http://localhost:8000/v1/health | python -m json.tool
```

**Providers** (session should be `session_live: true` for enabled ones):

```bash
curl -s http://localhost:8000/v1/providers | python -m json.tool
```

**Chat:**

```bash
curl -s -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"provider":"claude","message":"Say hello in one sentence."}' | python -m json.tool
```

**Dashboard:** open **http://localhost:8000/dashboard** in a browser.

Interactive API docs: **http://localhost:8000/docs**

---

## 7. Common problems

| Symptom | What to try |
|--------|--------------|
| **Login / session false** | Re-run `capture_session.py` or refresh cookies; ensure the site isn’t stuck on a CAPTCHA or “verify human” in a headed run first. |
| **Profile already in use / SingletonLock** | Quit every Chrome window using that profile; stop a duplicate server; delete stale `Singleton*` files only if no Chrome is running. |
| **`launch_persistent_context(channel=chrome) failed`** | Install **Google Chrome**. For directory profiles, Chromium alone is not enough. |
| **503 / session expired after deploy** | Cookies rotated or profile invalid; capture again. Prefer **`storage_state` JSON** or profile captured on the same OS/browser channel you use in production. |
| **Cloudflare or bot checks** | Often stricter in headless Chromium; persistent **Chrome** profile + non-headless capture usually helps; there is no guaranteed bypass. |
| **Docker + Claude profile dir** | Use **`.json` storage state** or cookies, or run on the host with Chrome — see section 1. |

---

## 8. Next steps

- **API details:** [README.md](README.md)  
- **Legal / risk:** [DISCLAIMER.md](DISCLAIMER.md)  
- **Debugging failed sends:** enable **`ENABLE_TRACING=true`** and inspect **`.o11y/`** (gitignored).

If something in this doc drifts from the code (env vars, paths), check **`.env.example`** and **`config.py`** in the repo.
