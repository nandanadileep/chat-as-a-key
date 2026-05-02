"""Network-first + DOM fallback parsing for non-Claude providers (reuses Claude JSON/SSE helpers)."""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Optional

from parsers.claude_parser import (
    ClaudeParseResult,
    Source,
    _extract_from_json,
    _parse_sse_body,
)

ParseResult = ClaudeParseResult


def _best_text_from_records(
    records: list[dict[str, Any]],
    *,
    is_api_error_snippet: Optional[Callable[[str], bool]] = None,
) -> tuple[str, Optional[dict[str, Any]]]:
    """Longest plausible assistant text; skip HTTP status >= 400 and optional API error snippets."""
    best = ""
    best_raw: Optional[dict[str, Any]] = None
    err_fn = is_api_error_snippet or (lambda _t: False)

    for rec in records:
        status = int(rec.get("status") or 0)
        if status >= 400:
            continue

        raw = rec.get("raw_json")
        text_piece = ""
        if raw is not None:
            text_piece = _extract_from_json(raw)
        if not text_piece:
            rt = rec.get("response_text") or ""
            ct = (rec.get("content_type") or "").lower()
            if "text/event-stream" in ct or rt.strip().startswith("data:"):
                text_piece = _parse_sse_body(rt)
            else:
                try:
                    parsed = json.loads(rt)
                    text_piece = _extract_from_json(parsed)
                except (json.JSONDecodeError, TypeError):
                    pass

        text_piece = (text_piece or "").strip()
        if not text_piece:
            continue
        if err_fn(text_piece):
            continue
        if len(text_piece) > len(best):
            best = text_piece
            best_raw = rec

    if best:
        return best, best_raw
    return "", None


def parse_generic_turn(
    *,
    provider: str,
    network_records: list[dict[str, Any]],
    dom_text: str,
    started_perf: float,
    errors: Optional[list[str]] = None,
    is_api_error_snippet: Optional[Callable[[str], bool]] = None,
) -> ParseResult:
    """Same resolution strategy as ``parse_claude_turn`` without Claude-specific error markers by default."""
    errs = list(errors or [])
    net_text, _ = _best_text_from_records(network_records, is_api_error_snippet=is_api_error_snippet)
    dom_clean = (dom_text or "").strip()
    source: Source = "network"
    final = net_text
    err_fn = is_api_error_snippet or (lambda _t: False)

    if err_fn(net_text) and dom_clean and not err_fn(dom_clean):
        final = dom_clean
        source = "dom"
    elif not final or (dom_clean and len(dom_clean) > len(final) and not err_fn(dom_clean)):
        if dom_clean:
            final = dom_clean
            source = "dom"
    latency_ms = int((time.perf_counter() - started_perf) * 1000)
    if not final:
        errs.append("empty_response")
        return ParseResult(
            provider=provider,
            status="error",
            text="",
            latency_ms=latency_ms,
            source=source,
            errors=errs,
        )
    return ParseResult(
        provider=provider,
        status="success",
        text=final,
        latency_ms=latency_ms,
        source=source,
        errors=errs,
    )
