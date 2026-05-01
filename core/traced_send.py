"""Failure-triggered CDP + snapshot tracing for any Playwright-backed provider."""

from __future__ import annotations

import logging
import os
import uuid
from typing import Awaitable, Callable, Optional, TypeVar

from playwright.async_api import Page

from browser.cdp_observer import CdpReadOnlyObserver
from browser.tracer import ObservabilityTracer, tracing_enabled

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _response_ok(resp: object) -> bool:
    msg = getattr(resp, "message", None) or ""
    return bool(str(msg).strip())


async def send_with_optional_failure_trace(
    *,
    page: Page,
    page_url_hint: str,
    send_impl: Callable[[], Awaitable[T]],
) -> T:
    """One normal attempt; if empty or exception and ENABLE_TRACING, one traced retry."""
    last_exc: Optional[BaseException] = None
    r: Optional[T] = None
    try:
        r = await send_impl()
    except BaseException as e:
        last_exc = e

    if r is not None and _response_ok(r):
        return r

    if not tracing_enabled():
        if last_exc is not None:
            raise last_exc
        assert r is not None
        return r

    run_id = str(uuid.uuid4())
    root = os.path.join(".o11y", run_id)
    os.makedirs(root, exist_ok=True)
    logger.info("Retrying with tracing enabled (hint=%s run_id=%s)", page_url_hint, run_id)

    cdp = CdpReadOnlyObserver(run_id, page_url_hint=page_url_hint)
    obs = ObservabilityTracer(run_id)
    r2: Optional[T] = None
    last_exc2: Optional[BaseException] = None
    try:
        try:
            await cdp.start()
        except Exception as e:
            logger.warning("CDP observer start failed (ignored): %s", e)
        try:
            await obs.start(page, snapshots_only=True)
        except Exception as e:
            logger.warning("Snapshot tracer start failed (ignored): %s", e)
        try:
            r2 = await send_impl()
        except BaseException as e:
            last_exc2 = e
    finally:
        try:
            await obs.stop()
        except Exception:
            pass
        try:
            await cdp.stop()
        except Exception:
            pass

    if r2 is not None and _response_ok(r2):
        return r2
    if last_exc2 is not None:
        raise last_exc2
    if last_exc is not None:
        raise last_exc
    if r2 is not None:
        return r2
    assert r is not None
    return r
