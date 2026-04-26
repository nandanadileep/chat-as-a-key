import json
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ProviderConfig:
    enabled: bool = False
    cookies_json: Optional[str] = None  # JSON string of cookie list
    storage_state: Optional[str] = None  # Path to Playwright storage state file

    @property
    def cookies(self) -> list:
        if self.cookies_json:
            try:
                return json.loads(self.cookies_json)
            except json.JSONDecodeError:
                return []
        return []


@dataclass
class AppConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"
    providers: dict[str, ProviderConfig] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "AppConfig":
        cfg = cls(
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "8000")),
            log_level=os.getenv("LOG_LEVEL", "info"),
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
