# chat-as-a-key

Self-hostable LLM proxy that lets you access Claude.ai, ChatGPT, Gemini, Grok, Perplexity, and Microsoft Copilot programmatically — no separate API key required. It uses Playwright to drive your existing browser sessions and exposes a clean, provider-agnostic REST API.

> **Disclaimer:** This project automates web UIs in ways that may violate the Terms of Service of Claude.ai, ChatGPT, Google Gemini, Grok, Perplexity, and Microsoft Copilot. Use it only with accounts you own and entirely at your own risk. The authors are not responsible for account suspensions or any other consequences.

---

## Quick start

### 1. Clone and configure

```bash
git clone https://github.com/yourname/chat-as-a-key
cd chat-as-a-key
cp .env.example .env
```

Edit `.env` and enable the providers you want:

```dotenv
CLAUDE_ENABLED=true
CLAUDE_COOKIES=[{"name":"sessionKey","value":"sk-ant-...","domain":"claude.ai","path":"/"}]
```

See [Exporting cookies](#exporting-cookies) below.

### 2. Run with Docker (recommended)

```bash
docker compose up --build
```

The API is now available at `http://localhost:8000`.

### 3. Run without Docker

```bash
pip install -r requirements.txt
playwright install chromium
cp .env.example .env   # fill in values
python server.py
```

---

## API

### POST /v1/chat

Send a message to any enabled provider.

```bash
curl -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "claude",
    "message": "Explain async/await in Python in two sentences."
  }'
```

**Multi-turn conversation** — pass the `conversation_id` returned from the first call:

```bash
curl -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "claude",
    "message": "Now give me a code example.",
    "conversation_id": "abc123"
  }'
```

**Response:**

```json
{
  "provider": "claude",
  "message": "async/await lets you write concurrent code...",
  "conversation_id": "abc123",
  "model": "claude",
  "timestamp": 1714000000.0
}
```

### GET /v1/providers

List all enabled providers and their session status.

```bash
curl http://localhost:8000/v1/providers
```

### GET /v1/health

Overall proxy health check.

```bash
curl http://localhost:8000/v1/health
```

### GET /v1/status/{provider}

Per-provider session check.

```bash
curl http://localhost:8000/v1/status/claude
```

### GET /dashboard

Web UI showing all providers, session status, and request counts. Auto-refreshes every 15 seconds.

```
http://localhost:8000/dashboard
```

---

## Exporting cookies

The easiest way to get valid session cookies is to use a browser extension like **Cookie-Editor** or **EditThisCookie**, log into the provider, and export the cookies as JSON.

Paste the JSON array directly into your `.env`:

```dotenv
CLAUDE_COOKIES=[{"name":"sessionKey","value":"YOUR_VALUE","domain":"claude.ai","path":"/","secure":true,"httpOnly":true}]
```

Alternatively, you can use a Playwright storage state file (includes cookies + localStorage), which is more robust for providers that use localStorage:

```bash
# Generate a storage state file for Claude
python -c "
import asyncio
from playwright.async_api import async_playwright

async def capture():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto('https://claude.ai')
        input('Log in manually, then press Enter...')
        await context.storage_state(path='sessions/claude.json')
        await browser.close()
        print('Saved to sessions/claude.json')

asyncio.run(capture())
"
```

Then in `.env`:

```dotenv
CLAUDE_STORAGE_STATE=sessions/claude.json
```

---

## Integrations

### LangChain

```python
from langchain_community.llms.base import LLM
from typing import Optional, List
import requests

class LLMBridge(LLM):
    provider: str = "claude"
    base_url: str = "http://localhost:8000"
    conversation_id: Optional[str] = None

    @property
    def _llm_type(self) -> str:
        return "chat-as-a-key"

    def _call(self, prompt: str, stop: Optional[List[str]] = None, **kwargs) -> str:
        resp = requests.post(
            f"{self.base_url}/v1/chat",
            json={"provider": self.provider, "message": prompt, "conversation_id": self.conversation_id},
        )
        resp.raise_for_status()
        data = resp.json()
        self.conversation_id = data.get("conversation_id")
        return data["message"]

llm = LLMBridge(provider="claude")
print(llm.invoke("What is the capital of France?"))
```

### LlamaIndex

```python
from llama_index.llms.custom import CustomLLM
from llama_index.core.llms import CompletionResponse
import requests

class LLMBridgeLLM(CustomLLM):
    provider: str = "claude"
    base_url: str = "http://localhost:8000"

    @property
    def metadata(self):
        from llama_index.core.llms import LLMMetadata
        return LLMMetadata(model_name=self.provider)

    def complete(self, prompt: str, **kwargs) -> CompletionResponse:
        resp = requests.post(
            f"{self.base_url}/v1/chat",
            json={"provider": self.provider, "message": prompt},
        )
        resp.raise_for_status()
        return CompletionResponse(text=resp.json()["message"])

    def stream_complete(self, prompt: str, **kwargs):
        raise NotImplementedError

llm = LLMBridgeLLM(provider="gemini")
print(llm.complete("Hello, world!").text)
```

### Any HTTP client

```python
import httpx

response = httpx.post(
    "http://localhost:8000/v1/chat",
    json={"provider": "chatgpt", "message": "Write a haiku about Docker."},
)
print(response.json()["message"])
```

---

## Configuration reference

| Variable | Default | Description |
|---|---|---|
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8000` | Listen port |
| `LOG_LEVEL` | `info` | Uvicorn log level |
| `{PROVIDER}_ENABLED` | `false` | Enable a provider |
| `{PROVIDER}_COOKIES` | — | JSON cookie array |
| `{PROVIDER}_STORAGE_STATE` | — | Path to Playwright state file |

Supported provider prefixes: `CLAUDE`, `CHATGPT`, `GEMINI`, `GROK`, `PERPLEXITY`, `COPILOT`.

---

## Project structure

```
chat-as-a-key/
  providers/
    base.py          # BaseProvider interface + shared data classes
    claude.py
    chatgpt.py
    gemini.py
    grok.py
    perplexity.py
    copilot.py
  server.py          # FastAPI app, routes, dashboard
  config.py          # .env loading
  docker-compose.yml
  Dockerfile
  requirements.txt
  .env.example
```

---

## Adding a new provider

1. Create `providers/yourprovider.py` implementing `BaseProvider`.
2. Add it to `PROVIDER_MAP` in `providers/__init__.py`.
3. Add the `YOURPROVIDER_ENABLED` / `YOURPROVIDER_COOKIES` vars to `.env.example`.

---

## License

MIT
