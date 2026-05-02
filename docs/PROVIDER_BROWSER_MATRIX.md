# Provider matrix (browser automation)

**Document version:** 2026-05-02 (update this date when you change provider behavior, capture flows, or reliability notes.)

chat-as-a-key drives **vendor web UIs** with Playwright and your saved session — not official chat APIs. Pass/fail depends on **session freshness**, **headless vs headed**, **site changes**, and **anti-automation** per product.

For a **committed smoke snapshot** of what `POST /v1/chat` did on a real dev machine, see the table in the root **[README.md](../README.md#smoke-test-results)** (regenerate after sessions or code change).

## Groq vs Grok (this repo)

| Name | What it is | In this repo? |
| --- | --- | --- |
| **Groq** | GroqCloud / LPU inference API (`groq.com`, OpenAI-compatible HTTP API) | **No** — not implemented; would be a different integration (API keys, not browser). |
| **Grok** | xAI consumer chat at **`grok.com`**, browser session | **Yes** — provider id `grok`. |

## Supported browser providers

These are the keys accepted by `POST /v1/chat` (`provider` field) and `PROVIDER_MAP` in code:

| `provider` | Site | Session capture |
| --- | --- | --- |
| `claude` | claude.ai | `python scripts/capture_session.py claude` (profile dir + optional export) |
| `chatgpt` | chatgpt.com | `python scripts/capture_session.py chatgpt` → `sessions/chatgpt.json` |
| `gemini` | gemini.google.com | `python scripts/capture_session.py gemini` |
| `grok` | grok.com | `python scripts/capture_session.py grok` |
| `perplexity` | www.perplexity.ai | `python scripts/capture_session.py perplexity` |
| `copilot` | copilot.microsoft.com | `python scripts/capture_session.py copilot` |

## Why some pass and others fail (article angles)

1. **Headless fingerprinting** — Some surfaces throttle or alter UI for headless Chrome; others are stricter than Chromium-with-`channel=chrome`.
2. **DOM drift** — Selectors target visible composer, stop buttons, assistant bubbles; A/B tests and redesigns break automation without code updates.
3. **Hidden vs visible controls** — e.g. a `textarea` in the tree for a11y while the real input is `contenteditable`; `wait_for(visible)` fails on the wrong node.
4. **Auth and redirects** — Expired `storage_state`, SSO walls, or geo/captcha flows return pages that never expose the composer.
5. **Implementation parity** — Providers use different launch paths (e.g. stealth Chrome vs stock Chromium); reliability is not uniform until aligned.

## Generate *your* pass/fail table

Use **one shell command per line** (and a real newline after `source .venv/bin/activate`). Pasting several commands on one line, or typing `activatepython`, breaks activation and can make argparse see stray words (for example `unrecognized arguments: # checklist`).

1. Enable every provider you have sessions for in `.env` (`*_ENABLED=true`, `*_STORAGE_STATE=...`).
2. Start the API in **one** terminal (only one process on the port):

   ```bash
   cd /path/to/chat-as-a-key
   source .venv/bin/activate
   python server.py
   ```

   If you see **`address already in use` (port 8000)**, another server is still running — stop it, or run `PORT=8001 python server.py` and `CHAT_AS_A_KEY_URL=http://127.0.0.1:8001 python scripts/smoke_test_providers.py`.

3. In a **second** terminal:

   ```bash
   cd /path/to/chat-as-a-key
   source .venv/bin/activate
   python scripts/smoke_test_providers.py --catalog
   python scripts/smoke_test_providers.py --markdown
   ```

4. Paste the Markdown block from step 3 into your article (note date, OS, and any `*_HEADED` env flags).

### Claude profile: `SingletonLock` / “profile already in use”

Claude uses a **persistent Chrome profile directory** (`sessions/claude_profile`). Only one Chrome may use it at a time. **Quit** any Chrome you opened with that profile (Chrome → Quit fully), stop other `python server.py` instances, then start again. Stale lock files are sometimes removed on retry; if it persists with no Chrome running, remove `SingletonLock`, `SingletonSocket`, and `SingletonCookie` inside that folder (only when no browser is using the profile).

