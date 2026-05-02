# Provider matrix (browser automation)

**Document version:** 2026-05-02

chat-as-a-key drives **vendor web UIs** with Playwright — not official chat APIs. **Claude** uses a dedicated orchestrator (`ClaudeOrchestrator`), network collector, and `claude_parser.py`. The other five providers share **`PlaywrightProviderBase`** in `providers/base.py` (Chrome channel + Chromium fallback, `HEADLESS` env, per-instance lock), **`GenericProviderNetworkCollector`**, and **`parsers/generic_parser.py`** (network-first + DOM fallback, same JSON/SSE extraction helpers as Claude).

## Groq vs Grok

| Name | What it is | In this repo? |
| --- | --- | --- |
| **Groq** | GroqCloud inference API | **No** |
| **Grok** | xAI **grok.com** browser session | **Yes** (`grok`) |

## Implementation matrix (honest)

| Provider | Composer strategy | Network intercept | Session probe | Known wall |
| --- | --- | --- | --- | --- |
| **Claude** | `ClaudeOrchestrator` selector list + send UI | `ClaudeNetworkCollector` (claude.ai `/api/`, stream, etc.) | `_wait_for_claude_ready` (login / challenge / SPA) | Cloudflare; profile `SingletonLock` |
| **ChatGPT** | Ordered contenteditable-first list (`_wait_first_visible_composer`) | `conversation.json`, `backend-api`, `completions`, `/api/` on chatgpt / openai hosts | Logged-out URL + **challenge HTML** + composer probe + app-surface optimistic | **Cloudflare / human verify**; headless UI drift |
| **Gemini** | `rich-textarea` / ql-editor ordered list | `generativelanguage.googleapis.com`, `batchrunquery`, google gemini paths | accounts.google redirect; challenge snapshot; composer or host fallback | Google login / consent; DOM changes |
| **Grok** | textarea / contenteditable list | `api.x.ai`, `grok.com/api`, chat-like paths | grok host + login path + challenge + composer / host fallback | xAI rate limits; UI changes |
| **Perplexity** | main `textarea` / contenteditable list | `/rest/v2`, `/socket.io`, perplexity `/api` | OAuth hosts + challenge + composer + host fallback | Bot walls; socket-only flows may parse thinly |
| **Copilot** | textarea / cib-text-input list | `sydney.bing.com`, `copilot.microsoft.com` `api` / `c4/api` | Microsoft login hosts + challenge + composer + host fallback | SSO / enterprise policy; Sydney API churn |

## Env quick reference

| Variable | Role |
| --- | --- |
| `HEADLESS` | Default **true** for all **`PlaywrightProviderBase`** subclasses. |
| `CHATGPT_HEADED` / `PERPLEXITY_HEADED` | When **true**, force **headed** Chrome for that provider (overrides `HEADLESS`). |
| `CLAUDE_HEADLESS` | Claude-only; **independent** of `HEADLESS`. |

## Regenerate README smoke table

With the server running:

```bash
source .venv/bin/activate
python scripts/smoke_test_providers.py --markdown
```

Paste the Markdown block into the root **README** if you want a pinned snapshot.

### Claude profile lock

See README / SETUP: only one process may use `sessions/claude_profile` at a time.
