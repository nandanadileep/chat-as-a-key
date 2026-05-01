from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

Source = Literal["network", "dom"]


@dataclass
class ClaudeParseResult:
    provider: str
    status: Literal["success", "error"]
    text: str
    latency_ms: int
    source: Source
    errors: list[str] = field(default_factory=list)


# Substrings from Claude XHR error payloads — must not beat real assistant text.
_CLAUDE_API_ERROR_MARKERS = (
    "this conversation could not be found",
    "conversation could not be found",
    "conversation not found",
    "unable to find this conversation",
    "unable to load conversation",
    "access denied",
    "unauthorized",
    "forbidden",
)


def _is_claude_api_error_snippet(text: str) -> bool:
    if not text or not str(text).strip():
        return True
    t = str(text).strip().lower()
    return any(m in t for m in _CLAUDE_API_ERROR_MARKERS)


def _walk_strings_for_keys(obj: Any, keys: set[str], out: list[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            lk = str(k).lower()
            if lk in keys and isinstance(v, str) and v.strip():
                out.append(v.strip())
            _walk_strings_for_keys(v, keys, out)
    elif isinstance(obj, list):
        for item in obj:
            _walk_strings_for_keys(item, keys, out)


def _extract_from_json(obj: Any) -> str:
    keys = {
        "text",
        "content",
        "completion",
        "message",
        "answer",
        "body",
        "delta",
        "value",
        "markdown",
    }
    found: list[str] = []
    _walk_strings_for_keys(obj, keys, found)
    if not found:
        return ""
    return max(found, key=len)


def _parse_sse_body(body: str) -> str:
    chunks: list[str] = []
    for block in body.split("\n\n"):
        for line in block.split("\n"):
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data in ("", "[DONE]"):
                continue
            try:
                j = json.loads(data)
                t = _extract_from_json(j)
                if t:
                    chunks.append(t)
            except json.JSONDecodeError:
                if data and not data.startswith("{"):
                    chunks.append(data)
    return "\n".join(chunks).strip()


def _best_text_from_network_records(records: list[dict[str, Any]]) -> tuple[str, Optional[dict[str, Any]]]:
    """Prefer longest plausible assistant text; skip HTTP errors and known API error strings."""
    best = ""
    best_raw: Optional[dict[str, Any]] = None

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

        if _is_claude_api_error_snippet(text_piece):
            continue

        if len(text_piece) > len(best):
            best = text_piece
            best_raw = rec

    if best:
        return best, best_raw
    return "", None


def parse_claude_turn(
    *,
    provider: str,
    network_records: list[dict[str, Any]],
    dom_text: str,
    started_perf: float,
    errors: Optional[list[str]] = None,
) -> ClaudeParseResult:
    errs = list(errors or [])
    net_text, _ = _best_text_from_network_records(network_records)
    dom_clean = (dom_text or "").strip()
    source: Source = "network"
    final = net_text

    if _is_claude_api_error_snippet(net_text) and dom_clean and not _is_claude_api_error_snippet(dom_clean):
        final = dom_clean
        source = "dom"
    elif not final or (dom_clean and len(dom_clean) > len(final) and not _is_claude_api_error_snippet(dom_clean)):
        if dom_clean:
            final = dom_clean
            source = "dom"
    latency_ms = int((time.perf_counter() - started_perf) * 1000)
    if not final:
        errs.append("empty_response")
        return ClaudeParseResult(
            provider=provider,
            status="error",
            text="",
            latency_ms=latency_ms,
            source=source,
            errors=errs,
        )
    return ClaudeParseResult(
        provider=provider,
        status="success",
        text=final,
        latency_ms=latency_ms,
        source=source,
        errors=errs,
    )
