import asyncio
import logging
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from config import config
from providers import PROVIDER_MAP, BaseProvider, ChatResponse, ProviderStatus

logging.basicConfig(level=config.log_level.upper())
logger = logging.getLogger(__name__)

# Active provider instances
_providers: dict[str, BaseProvider] = {}
# Serialize /v1/chat (and session reuse) per provider — one Page per instance.
_provider_chat_locks: dict[str, asyncio.Lock] = {}
# Per-provider request counters  {provider: count}
_request_counts: dict[str, int] = defaultdict(int)
# Per-provider last-error cache
_last_errors: dict[str, Optional[str]] = defaultdict(lambda: None)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _init_providers()
    yield
    await _shutdown_providers()


app = FastAPI(
    title="chat-as-a-key",
    description="Self-hostable LLM proxy over chat UIs",
    version="1.0.0",
    lifespan=lifespan,
)


def _chat_lock(name: str) -> asyncio.Lock:
    lk = _provider_chat_locks.get(name)
    if lk is None:
        lk = asyncio.Lock()
        _provider_chat_locks[name] = lk
    return lk


async def _init_providers() -> None:
    for name, pcfg in config.providers.items():
        if not pcfg.enabled:
            continue
        cls = PROVIDER_MAP.get(name)
        if cls is None:
            logger.warning("Unknown provider: %s", name)
            continue
        if name == "claude":
            instance = cls(cookies=pcfg.cookies, storage_state=pcfg.storage_state)
        else:
            instance = cls(
                cookies=pcfg.cookies,
                storage_state=pcfg.storage_state,
                playwright_headless=config.playwright_headless,
            )
        try:
            ok = await instance.login()
            if ok:
                logger.info("Provider %s logged in successfully", name)
            else:
                logger.warning("Provider %s login returned False — session may not be active", name)
                _last_errors[name] = "Login failed — check your cookies or storage state"
        except Exception as e:
            logger.error("Provider %s failed to initialize: %s", name, e)
            _last_errors[name] = str(e)
        _providers[name] = instance


async def _shutdown_providers() -> None:
    for name, provider in list(_providers.items()):
        try:
            await asyncio.wait_for(provider.close(), timeout=45.0)
        except asyncio.TimeoutError:
            logger.warning("Timeout closing provider %s (browser may still exit on its own)", name)
        except Exception as e:
            logger.warning("Error closing provider %s: %s", name, e)


async def _get_active_provider(name: str) -> BaseProvider:
    if name not in _providers:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' is not configured or not enabled")
    provider = _providers[name]
    alive = await provider.check_session()
    if not alive:
        logger.info("Session expired for %s, attempting reset", name)
        try:
            ok = await provider.reset_session()
            if not ok:
                _last_errors[name] = "Session expired and reset failed"
                raise HTTPException(status_code=503, detail=f"Provider '{name}' session expired and could not be reset")
            _last_errors[name] = None
        except HTTPException:
            raise
        except Exception as e:
            _last_errors[name] = str(e)
            raise HTTPException(status_code=503, detail=f"Provider '{name}' reset error: {e}")
    return provider


# --- Request/Response schemas ---

class ChatRequest(BaseModel):
    provider: str
    message: str
    conversation_id: Optional[str] = None


class ChatResponseSchema(BaseModel):
    provider: str
    message: str
    conversation_id: Optional[str] = None
    model: Optional[str] = None
    timestamp: float


class ProviderInfo(BaseModel):
    name: str
    enabled: bool
    session_live: bool
    error: Optional[str] = None
    request_count: int


class HealthResponse(BaseModel):
    status: str
    active_providers: int
    total_requests: int
    timestamp: float


# --- API routes ---

@app.post("/v1/chat", response_model=ChatResponseSchema)
async def chat(req: ChatRequest) -> ChatResponseSchema:
    async with _chat_lock(req.provider):
        provider = await _get_active_provider(req.provider)
        _request_counts[req.provider] += 1
        try:
            result: ChatResponse = await provider.send_message(req.message, req.conversation_id)
            _last_errors[req.provider] = None
            return ChatResponseSchema(
                provider=result.provider,
                message=result.message,
                conversation_id=result.conversation_id,
                model=result.model,
                timestamp=result.timestamp,
            )
        except HTTPException:
            raise
        except Exception as e:
            _last_errors[req.provider] = str(e)
            raise HTTPException(status_code=500, detail=f"Provider error: {e}")


@app.get("/v1/providers", response_model=list[ProviderInfo])
async def list_providers() -> list[ProviderInfo]:
    result = []
    checks = {
        name: provider.check_session()
        for name, provider in _providers.items()
    }
    statuses = await asyncio.gather(*checks.values(), return_exceptions=True)
    status_map = dict(zip(checks.keys(), statuses))

    for name, pcfg in config.providers.items():
        if not pcfg.enabled:
            continue
        live = status_map.get(name, False)
        if isinstance(live, Exception):
            live = False
        result.append(ProviderInfo(
            name=name,
            enabled=True,
            session_live=bool(live),
            error=_last_errors.get(name),
            request_count=_request_counts[name],
        ))
    return result


@app.get("/v1/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    active = len(_providers)
    total = sum(_request_counts.values())
    return HealthResponse(
        status="ok",
        active_providers=active,
        total_requests=total,
        timestamp=time.time(),
    )


@app.get("/v1/status/{provider}", response_model=ProviderInfo)
async def provider_status(provider: str) -> ProviderInfo:
    if provider not in config.providers or not config.providers[provider].enabled:
        raise HTTPException(status_code=404, detail=f"Provider '{provider}' not configured")

    instance = _providers.get(provider)
    if instance is None:
        return ProviderInfo(
            name=provider,
            enabled=False,
            session_live=False,
            error="Provider failed to initialize",
            request_count=0,
        )

    try:
        live = await instance.check_session()
    except Exception as e:
        live = False
        _last_errors[provider] = str(e)

    return ProviderInfo(
        name=provider,
        enabled=True,
        session_live=live,
        error=_last_errors.get(provider),
        request_count=_request_counts[provider],
    )


# --- Web dashboard ---

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    provider_rows = ""
    for name, pcfg in config.providers.items():
        if not pcfg.enabled:
            continue
        instance = _providers.get(name)
        if instance:
            try:
                live = await instance.check_session()
            except Exception:
                live = False
        else:
            live = False

        status_class = "live" if live else "dead"
        status_label = "Live" if live else "Expired / Error"
        error = _last_errors.get(name) or ""
        count = _request_counts[name]

        provider_rows += f"""
        <tr>
          <td><strong>{name}</strong></td>
          <td><span class="badge {status_class}">{status_label}</span></td>
          <td>{count}</td>
          <td class="error-cell">{error}</td>
        </tr>
        """

    total_requests = sum(_request_counts.values())

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>chat-as-a-key dashboard</title>
  <meta http-equiv="refresh" content="15" />
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0f1117;
      color: #e2e8f0;
      padding: 2rem;
    }}
    h1 {{ font-size: 1.6rem; font-weight: 700; margin-bottom: 0.25rem; }}
    .subtitle {{ color: #718096; font-size: 0.875rem; margin-bottom: 2rem; }}
    .stat-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 1rem;
      margin-bottom: 2rem;
    }}
    .stat-card {{
      background: #1a1d2e;
      border: 1px solid #2d3748;
      border-radius: 8px;
      padding: 1.25rem;
    }}
    .stat-card .label {{ font-size: 0.75rem; color: #718096; text-transform: uppercase; letter-spacing: .05em; }}
    .stat-card .value {{ font-size: 2rem; font-weight: 700; margin-top: 0.25rem; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: #1a1d2e;
      border: 1px solid #2d3748;
      border-radius: 8px;
      overflow: hidden;
    }}
    th, td {{
      text-align: left;
      padding: 0.85rem 1rem;
      font-size: 0.875rem;
      border-bottom: 1px solid #2d3748;
    }}
    th {{ color: #718096; font-weight: 600; text-transform: uppercase; font-size: 0.75rem; letter-spacing: .05em; }}
    tr:last-child td {{ border-bottom: none; }}
    .badge {{
      display: inline-block;
      padding: 0.2rem 0.65rem;
      border-radius: 9999px;
      font-size: 0.75rem;
      font-weight: 600;
    }}
    .badge.live {{ background: #1a3a2a; color: #68d391; }}
    .badge.dead {{ background: #3a1a1a; color: #fc8181; }}
    .error-cell {{ color: #fc8181; font-size: 0.8rem; max-width: 320px; word-break: break-word; }}
    .refresh-note {{ margin-top: 1rem; color: #4a5568; font-size: 0.75rem; }}
  </style>
</head>
<body>
  <h1>chat-as-a-key</h1>
  <p class="subtitle">LLM proxy dashboard &mdash; auto-refreshes every 15 seconds</p>

  <div class="stat-grid">
    <div class="stat-card">
      <div class="label">Active Providers</div>
      <div class="value">{len(_providers)}</div>
    </div>
    <div class="stat-card">
      <div class="label">Total Requests</div>
      <div class="value">{total_requests}</div>
    </div>
  </div>

  <table>
    <thead>
      <tr>
        <th>Provider</th>
        <th>Session</th>
        <th>Requests</th>
        <th>Last Error</th>
      </tr>
    </thead>
    <tbody>
      {provider_rows if provider_rows else '<tr><td colspan="4" style="color:#718096;text-align:center;padding:2rem">No providers enabled. Set PROVIDER_ENABLED=true in .env</td></tr>'}
    </tbody>
  </table>

  <p class="refresh-note">Page refreshes automatically. <a href="/docs" style="color:#63b3ed">API docs &rarr;</a></p>
</body>
</html>"""
    return HTMLResponse(content=html)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host=config.host, port=config.port, log_level=config.log_level, reload=False)
