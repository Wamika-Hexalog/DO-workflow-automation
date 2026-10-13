"""Resolve a no-login shareable live-view URL for a Browserbase session.

The Stagehand SDK only yields a session id and the login-gated replay URL
(browserbase.com/sessions/{id}). The Browserbase REST API's debug endpoint
returns ``debuggerFullscreenUrl`` — a shareable live view that needs no login.
We hit it directly with the existing API key (the same REST surface the file
download path already uses), so no extra dependency is required.

Best-effort by design: any failure returns None — a missing live link must never
break a browser run.
"""

from __future__ import annotations

import os

import httpx
import structlog

logger = structlog.get_logger(__name__)

_DEBUG_URL = "https://api.browserbase.com/v1/sessions/{session_id}/debug"


async def fetch_browserbase_live_url(
    session_id: str,
    *,
    api_key: str | None = None,
    timeout: float = 15.0,
) -> str | None:
    """Return a shareable, no-login live-view URL for an active session, or None.

    Args:
        session_id: Browserbase session id (StagehandClient.session_id).
        api_key: overrides BROWSERBASE_API_KEY (mainly for tests).
    """
    key = api_key or os.environ.get("BROWSERBASE_API_KEY", "")
    if not session_id or not key:
        return None
    url = _DEBUG_URL.format(session_id=session_id)
    try:
        async with httpx.AsyncClient(timeout=timeout) as http:
            resp = await http.get(url, headers={"x-bb-api-key": key})
        if resp.status_code != 200:
            logger.warning(
                "browserbase.debug_non_200", status_code=resp.status_code, session_id=session_id
            )
            return None
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 — best-effort; never raise into a run
        logger.warning("browserbase.debug_failed", error=str(exc), session_id=session_id)
        return None
    return data.get("debuggerFullscreenUrl") or data.get("debuggerUrl")
