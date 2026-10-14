# copied from python-brain @ 4bb5af8; imports changed to this package, nothing else
"""Microsoft Graph webhook subscription management for python-brain.

Adapted from email-ocr/app/connectors/graph_subscription.py.
Import-root changed to shared.config / shared.connectors.outlook.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import structlog

from ._settings import settings
from .http import error_code_from_response

logger = structlog.get_logger(__name__)

GRAPH_SUBSCRIPTIONS_URL = "https://graph.microsoft.com/v1.0/subscriptions"
SUBSCRIPTION_LIFETIME_MINUTES = 10_070  # max allowed by Graph for Outlook messages

_RANDOM_CLIENT_STATE = secrets.token_hex(16)


def _client_state() -> str:
    # Local change: the configured secret, so the webhook can check it. The engine's random
    # per-process value is never verified.
    return settings.graph_client_state or _RANDOM_CLIENT_STATE


def _notification_url() -> str:
    base = (settings.webhook_base_url or "").rstrip("/")
    # Local change: this app serves the webhook at /api/webhooks, not python-brain's /api/v1/webhooks.
    return f"{base}/api/webhooks/microsoft-graph"


def get_webhook_notification_url() -> str:
    return _notification_url()


def _resource() -> str:
    email = (settings.outlook_recipient_email or "").strip()
    if not email:
        return ""
    return f"users/{email}/mailFolders('Inbox')/messages"


def _expiration_iso() -> str:
    expires = datetime.now(timezone.utc) + timedelta(minutes=SUBSCRIPTION_LIFETIME_MINUTES)
    return expires.strftime("%Y-%m-%dT%H:%M:%S.0000000Z")


async def _get_token() -> str:
    from .outlook import OutlookClient

    client = OutlookClient()
    return await client._get_access_token()


async def _list_subscriptions(token: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        r = await http.get(
            GRAPH_SUBSCRIPTIONS_URL,
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        return r.json().get("value") or []


async def _delete_subscription(sub_id: str, token: str) -> None:
    async with httpx.AsyncClient(timeout=30.0) as http:
        r = await http.delete(
            f"{GRAPH_SUBSCRIPTIONS_URL}/{sub_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()


async def _create_subscription(token: str, resource: str | None = None) -> dict[str, Any]:
    notification_url = _notification_url()
    payload = {
        "resource": resource or _resource(),
        "changeType": "created",
        "notificationUrl": notification_url,
        "lifecycleNotificationUrl": notification_url,
        "expirationDateTime": _expiration_iso(),
        "clientState": _client_state(),
        "latestSupportedTlsVersion": "v1_2",
    }
    logger.info("Creating Graph subscription", resource=payload["resource"])
    async with httpx.AsyncClient(timeout=30.0) as http:
        r = await http.post(
            GRAPH_SUBSCRIPTIONS_URL,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
        )
        if not r.is_success:
            logger.error(
                "Graph subscription POST failed",
                status_code=r.status_code,
                error_code=error_code_from_response(r),
                handled=False,
            )
        r.raise_for_status()
        return r.json()


async def list_graph_subscriptions() -> list[dict[str, Any]]:
    """List current Graph subscriptions (diagnostic, no clientState exposed)."""
    if not all(
        [settings.outlook_client_id, settings.outlook_client_secret, settings.outlook_tenant_id]
    ):
        return []
    try:
        token = await _get_token()
        raw = await _list_subscriptions(token)
    except Exception as e:
        logger.warning("Could not list Graph subscriptions", error=str(e))
        return []
    return [
        {
            "id": s.get("id"),
            "resource": s.get("resource"),
            "changeType": s.get("changeType"),
            "notificationUrl": s.get("notificationUrl"),
            "expirationDateTime": s.get("expirationDateTime"),
        }
        for s in raw
    ]


async def ensure_graph_subscription() -> dict[str, Any]:
    """Create or renew Graph subscription. Returns {"ok": True, ...} or {"ok": False, "error": ...}."""
    base = (settings.webhook_base_url or "").strip()
    if not base:
        return {"ok": False, "error": "WEBHOOK_BASE_URL is not set"}

    if not all(
        [settings.outlook_client_id, settings.outlook_client_secret, settings.outlook_tenant_id]
    ):
        return {"ok": False, "error": "Outlook credentials not configured"}

    resource = _resource()
    if not resource:
        return {"ok": False, "error": "outlook_recipient_email not set"}

    notification_url = _notification_url()
    logger.info("Ensuring Graph subscription", resource=resource, notification_url=notification_url)

    try:
        token = await _get_token()
    except Exception as e:
        logger.error("Could not get Graph token", error=str(e), exc_info=True, handled=False)
        return {"ok": False, "error": f"Failed to get Graph token: {e!s}"}

    try:
        existing = await _list_subscriptions(token)
        for sub in existing:
            sub_resource = (sub.get("resource") or "").lstrip("/").lower()
            sub_url = (sub.get("notificationUrl") or "").strip()
            expected_resource = resource.lstrip("/").lower()
            if sub_url == notification_url:
                # If this subscription watches a different mailbox, delete it and create a fresh one.
                if sub_resource != expected_resource:
                    sub_id = sub.get("id")
                    logger.info(
                        "Graph subscription resource mismatch — deleting stale subscription",
                        id=sub_id,
                        sub_resource=sub_resource,
                        expected_resource=expected_resource,
                    )
                    if sub_id:
                        try:
                            await _delete_subscription(sub_id, token)
                        except httpx.HTTPStatusError as e:
                            if e.response.status_code != 404:
                                raise
                    continue

                # Reuse if still valid for more than 2 days — avoids validation handshake on every restart
                expiry_str = sub.get("expirationDateTime") or ""
                try:
                    expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
                    still_valid = expiry > datetime.now(timezone.utc) + timedelta(days=2)
                except Exception:
                    still_valid = False

                if still_valid:
                    logger.info(
                        "Graph subscription valid — reusing",
                        id=sub.get("id"),
                        expiry=expiry_str,
                        resource=sub_resource,
                    )
                    return {
                        "ok": True,
                        "subscription_id": sub.get("id"),
                        "expirationDateTime": expiry_str,
                        "notification_url": notification_url,
                        "resource": sub.get("resource"),
                    }

                # Expired or expiring soon — delete and recreate
                sub_id = sub.get("id")
                if sub_id:
                    try:
                        await _delete_subscription(sub_id, token)
                        logger.info("Deleted expired subscription", id=sub_id)
                    except httpx.HTTPStatusError as e:
                        if e.response.status_code != 404:
                            raise

        result = await _create_subscription(token, resource=resource)
        logger.info(
            "Graph subscription created",
            id=result.get("id"),
            expiry=result.get("expirationDateTime"),
        )
        return {
            "ok": True,
            "subscription_id": result.get("id"),
            "expirationDateTime": result.get("expirationDateTime"),
            "notification_url": notification_url,
            "resource": result.get("resource"),
        }

    except httpx.HTTPStatusError as e:
        error_code = error_code_from_response(e.response)
        fields: dict[str, Any] = {"http_status": e.response.status_code}
        if error_code:
            fields["error_code"] = error_code
        logger.error(
            "Graph subscription HTTP error",
            handled=False,
            **fields,
        )
        detail = f": {error_code}" if error_code else ""
        return {"ok": False, "error": f"Graph API {e.response.status_code}{detail}"}
    except Exception as e:
        logger.error("Graph subscription failed", error=str(e), exc_info=True, handled=False)
        return {"ok": False, "error": str(e)}
