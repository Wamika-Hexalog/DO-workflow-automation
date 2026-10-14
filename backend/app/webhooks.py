"""Microsoft Graph webhook, from python-brain's api/routers/webhooks.py, trimmed to what the
prototype needs. Expose only /api/webhooks/* through the tunnel: /api/inngest in dev mode
does not check signatures.

    GET  /api/webhooks/microsoft-graph   subscription validation
    POST /api/webhooks/microsoft-graph   change notifications → EmailService:InboundMessage
    POST /api/webhooks/graph/subscribe   create or renew the subscription (dev)
    GET  /api/webhooks/status            diagnostics
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import inngest
from fastapi import APIRouter, FastAPI, Request, Response

from .connectors.graph_subscription import (
    ensure_graph_subscription,
    get_webhook_notification_url,
    list_graph_subscriptions,
)
from .workflows.events import FORCE_REPROCESS_KEY, EventNames

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["webhooks"])

_RENEWAL_SECONDS = 6 * 60 * 60
_last_notification_at: datetime | None = None
_last_events_sent = 0


def _message_id(resource: str | None, resource_data: dict[str, Any] | None) -> str | None:
    if resource_data and isinstance(resource_data.get("id"), str):
        return str(resource_data["id"])
    match = re.search(r"[Mm]essages\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", resource or "")
    return match.group(1) if match else None


@router.get("/webhooks/microsoft-graph")
def graph_validation(request: Request) -> Response:
    token = request.query_params.get("validationToken")
    if token:
        return Response(content=token, media_type="text/plain")
    return Response(status_code=400, content="Missing validationToken")


@router.post("/webhooks/microsoft-graph")
async def graph_notification(request: Request) -> Response:
    global _last_notification_at, _last_events_sent
    token = request.query_params.get("validationToken")
    if token:
        return Response(content=token, status_code=200, media_type="text/plain")

    rt = request.app.state.rt
    raw = await request.body()
    try:
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return Response(status_code=202)

    _last_notification_at, _last_events_sent = datetime.now(timezone.utc), 0
    expected = rt.settings.graph_client_state
    events: list[inngest.Event] = []
    unreadable = 0
    for item in (i for i in (body.get("value") or []) if i and not i.get("lifecycleEvent")):
        if expected and not hmac.compare_digest(str(item.get("clientState") or ""), expected):
            logger.warning("Graph notification dropped: clientState does not match")
            continue
        message_id = _message_id(item.get("resource"), item.get("resourceData"))
        if not message_id:
            unreadable += 1
            continue
        events.append(
            inngest.Event(
                name=EventNames.EMAIL_INBOUND,
                # "" is the normal path: a Graph redelivery inside 24h collapses onto the first run.
                data={"message_id": message_id, "subscription_id": item.get("subscriptionId"), FORCE_REPROCESS_KEY: ""},
            )
        )
    if events:
        try:
            await rt.inngest.send(events)
            _last_events_sent = len(events)
        except Exception:
            # A 5xx makes Graph redeliver. A 2xx would lose the mail for good.
            logger.exception("Could not send %d inbound-mail events to Inngest", len(events))
            return Response(status_code=503)
    return Response(status_code=503 if unreadable else 202)


@router.post("/webhooks/graph/subscribe")
async def graph_subscribe() -> dict[str, Any]:
    return await ensure_graph_subscription()


@router.get("/webhooks/status")
async def webhook_status(request: Request) -> dict[str, Any]:
    settings = request.app.state.rt.settings
    return {
        "notification_url": get_webhook_notification_url(),
        "last_notification_at": _last_notification_at,
        "last_events_sent": _last_events_sent,
        "subscriptions": await list_graph_subscriptions() if settings.outlook_client_id else [],
        "inject_code_prefix": settings.inject_code_prefix,
    }


async def _subscription_loop() -> None:
    await asyncio.sleep(5)
    while True:
        result = await ensure_graph_subscription()
        logger.info("Graph subscription checked: ok=%s %s", result.get("ok"), result.get("error") or "")
        await asyncio.sleep(_RENEWAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = app.state.rt.settings
    task: asyncio.Task[None] | None = None
    if settings.webhook_base_url and settings.outlook_client_id:
        task = asyncio.create_task(_subscription_loop())
    else:
        logger.info("Graph subscription skipped: set WEBHOOK_BASE_URL and OUTLOOK_* to receive mail")
    if settings.outlook_client_id and not settings.graph_client_state:
        logger.warning("GRAPH_CLIENT_STATE is not set: the webhook does not check clientState")
    yield
    if task:
        task.cancel()
