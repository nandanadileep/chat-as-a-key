"""Extra Chromium args when failure CDP tracing is enabled (ENABLE_TRACING)."""

from __future__ import annotations

import os


def chromium_tracing_args() -> list[str]:
    if os.getenv("ENABLE_TRACING", "").lower() not in ("1", "true", "yes"):
        return []
    port = int(os.getenv("CDP_DEBUG_PORT", "9222"))
    return [f"--remote-debugging-port={port}"]
