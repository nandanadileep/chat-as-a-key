import json
import os
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

_SAME_SITE_MAP = {
    "strict": "Strict",
    "lax": "Lax",
    "none": "None",
    "no_restriction": "None",
    "unspecified": "None",
}


def _normalize_cookie(c: dict) -> dict:
    out = {
        "name": c["name"],
        "value": c["value"],
        "domain": c.get("domain", ""),
        "path": c.get("path", "/"),
        "secure": bool(c.get("secure", False)),
        "httpOnly": bool(c.get("httpOnly", False)),
        "sameSite": _SAME_SITE_MAP.get(str(c.get("sameSite") or "").lower(), "None"),
    }
    exp = c.get("expirationDate") or c.get("expires")
    if exp is not None:
        out["expires"] = float(exp)
    return out


@dataclass
class ProviderConfig:
    enabled: bool = False
    cookies_json: Optional[str] = None  # JSON string of cookie list
    storage_state: Optional[str] = None  # Path to Playwright storage state file

    @property
    def cookies(self) -> list:
        if self.cookies_json:
            try:
                raw = json.loads(self.cookies_json)
                return [_normalize_cookie(c) for c in raw]
            except json.JSONDecodeError:
                return []
        return []


@dataclass
class AppConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"
    """When True, Claude's BrowserSessionManager uses headless Chrome (better for Docker/CI)."""
    claude_headless: bool = False
    providers: dict[str, ProviderConfig] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "AppConfig":
        cfg = cls(
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "8000")),
            log_level=os.getenv("LOG_LEVEL", "info"),
            claude_headless=os.getenv("CLAUDE_HEADLESS", "false").lower() in ("1", "true", "yes"),
        )

        provider_names = ["claude", "chatgpt", "gemini", "grok", "perplexity", "copilot"]
        for name in provider_names:
            prefix = name.upper()
            enabled = os.getenv(f"{prefix}_ENABLED", "false").lower() == "true"
            cookies_json = os.getenv(f"{prefix}_COOKIES", None)
            storage_state = os.getenv(f"{prefix}_STORAGE_STATE", None)
            cfg.providers[name] = ProviderConfig(
                enabled=enabled,
                cookies_json=cookies_json,
                storage_state=storage_state,
            )

        return cfg


config = AppConfig.from_env()
