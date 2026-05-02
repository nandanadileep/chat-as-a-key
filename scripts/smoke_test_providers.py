#!/usr/bin/env python3
"""
Hit each *enabled* provider on a running chat-as-a-key server (POST /v1/chat).

Prerequisites:
  1. Fill .env (enable providers you want and set cookies / storage_state).
  2. Start the server:  python server.py   or   docker compose up
  3. Run from repo root:

       python scripts/smoke_test_providers.py

  Optional:
    --base-url URL   --timeout SECONDS
    --list-only      only print enabled provider names
    --catalog        print every supported provider + enabled/disabled from .env
    --markdown       after a full run, print a Markdown results table (for README / articles)

  Each POST can take minutes (Playwright on the server). The script prints progress
  before and after every provider so the terminal does not look frozen.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv


def _log(msg: str) -> None:
    print(msg, flush=True)

ROOT = Path(__file__).resolve().parent.parent
# All browser-backed UIs wired in server.py / PROVIDER_MAP (not the Groq inference API).
PROVIDER_NAMES = ("claude", "chatgpt", "gemini", "grok", "perplexity", "copilot")
PROMPT = "Reply with exactly one word: pong"


def _enabled_providers() -> list[str]:
    out: list[str] = []
    for name in PROVIDER_NAMES:
        if os.getenv(f"{name.upper()}_ENABLED", "false").lower() == "true":
            out.append(name)
    return out


def _catalog_lines() -> list[str]:
    """Every supported provider + ENABLED flag (for docs / article reproducibility)."""
    lines = ["Supported providers (set *_ENABLED=true and session cookies to test):", ""]
    w = max(len(n) for n in PROVIDER_NAMES)
    for name in PROVIDER_NAMES:
        en = os.getenv(f"{name.upper()}_ENABLED", "false").lower() == "true"
        lines.append(f"  {name:{w}}  {'enabled' if en else 'disabled'}")
    return lines


def _markdown_table(results: list[tuple[str, str, str]]) -> str:
    rows = ["| Provider | Result | Notes |", "| --- | --- | --- |"]
    for name, status, detail in results:
        d = detail.replace("|", "\\|").replace("\n", " ")
        if len(d) > 240:
            d = d[:237] + "…"
        rows.append(f"| `{name}` | **{status}** | {d} |")
    return "\n".join(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test /v1/chat for each enabled provider.")
    parser.add_argument(
        "--base-url",
        default=os.getenv("CHAT_AS_A_KEY_URL", "http://127.0.0.1:8000"),
        help="Running server base URL (default: env CHAT_AS_A_KEY_URL or http://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("SMOKE_TIMEOUT", "300")),
        help="Per-request timeout in seconds (default 300; browser sends can be slow)",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="Print which providers are enabled in .env and exit (no HTTP)",
    )
    parser.add_argument(
        "--catalog",
        action="store_true",
        help="Print all supported providers and enabled/disabled from .env; exit (no HTTP)",
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="After the run, also print a Markdown table (paste into README / Medium)",
    )
    args = parser.parse_args()

    os.chdir(ROOT)
    env_path = ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        print(f"Warning: no {env_path} — using process environment only", file=sys.stderr)
        _log("(Tip: run from repo root or ensure .env exists — otherwise no *_ENABLED flags load.)")

    enabled = _enabled_providers()
    if args.catalog:
        for line in _catalog_lines():
            _log(line)
        return 0

    if args.list_only:
        if not enabled:
            _log("No providers enabled (set *_ENABLED=true in .env).")
            return 2
        _log("Enabled providers: " + ", ".join(enabled))
        return 0

    if not enabled:
        print(
            "No providers enabled. Set at least one of:\n"
            "  CLAUDE_ENABLED, CHATGPT_ENABLED, GEMINI_ENABLED, GROK_ENABLED,\n"
            "  PERPLEXITY_ENABLED, COPILOT_ENABLED\n"
            "to true in .env (plus cookies or STORAGE_STATE).",
            file=sys.stderr,
        )
        return 2

    base = args.base_url.rstrip("/")
    health_url = f"{base}/v1/health"
    chat_url = f"{base}/v1/chat"

    _log("chat-as-a-key smoke test")
    _log(f"  Server:        {base}")
    _log(f"  Timeout/chat:  {args.timeout}s (browser work on the server often takes 1–5+ minutes each)")
    _log(f"  Providers:     {', '.join(enabled)}")
    _log("")

    timeout = httpx.Timeout(args.timeout, connect=30.0)
    results: list[tuple[str, str, str]] = []  # name, status, detail

    with httpx.Client(timeout=timeout) as client:
        _log(f"GET {health_url} …")
        try:
            r = client.get(health_url)
            r.raise_for_status()
            _log(f"  ok ({r.status_code})")
        except Exception as e:
            print(f"Cannot reach server at {health_url!r}: {e}", file=sys.stderr)
            print("Start the API first:  python server.py  or  docker compose up", file=sys.stderr)
            return 1

        for i, name in enumerate(enabled, start=1):
            _log("")
            _log(f"[{i}/{len(enabled)}] POST /v1/chat  provider={name!r}  (waiting on server — screen may look idle here) …")
            t0 = time.monotonic()
            try:
                r = client.post(
                    chat_url,
                    json={"provider": name, "message": PROMPT},
                    headers={"Content-Type": "application/json"},
                )
                elapsed = time.monotonic() - t0
                if r.status_code != 200:
                    detail = f"HTTP {r.status_code}: {r.text[:500]}"
                    results.append((name, "FAIL", detail))
                    _log(f"  FAIL after {elapsed:.1f}s — {detail[:200]}")
                    continue
                data = r.json()
                msg = data.get("message") or ""
                snippet = (msg[:120] + "…") if len(msg) > 120 else msg
                results.append((name, "OK", snippet))
                _log(f"  OK after {elapsed:.1f}s — {snippet!r}")
            except httpx.TimeoutException:
                results.append((name, "FAIL", f"timeout after {args.timeout}s"))
                _log(f"  FAIL — no response within {args.timeout}s")
            except Exception as e:
                results.append((name, "FAIL", str(e)))
                _log(f"  FAIL — {e}")

    _log("")
    _log("— summary —")
    w = max(len(n) for n, _, _ in results)
    for name, status, detail in results:
        line = f"{name:{w}}  {status:4}  {detail}"
        _log(line)

    if args.markdown and results:
        _log("")
        _log("— markdown (copy for README / article) —")
        _log(_markdown_table(results))

    failed = sum(1 for _, s, _ in results if s != "OK")
    if failed:
        print(f"\n{failed}/{len(results)} provider(s) failed.", file=sys.stderr)
        return 1
    print(f"\nAll {len(results)} enabled provider(s) returned 200.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
