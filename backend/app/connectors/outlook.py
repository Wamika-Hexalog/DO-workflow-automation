# copied from python-brain @ 4bb5af8; imports changed to this package, nothing else
"""Microsoft Graph API client for Outlook email operations.

Copied from email-ocr/app/connectors/outlook.py.
Only import paths changed: app.config → shared.config, app.connectors → shared.connectors.
"""

import asyncio
import structlog
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import httpx
from msal import ConfidentialClientApplication

from ._settings import settings
from .http import error_code_from_response
from .outlook_helpers import (
    build_message_filter,
    download_single_attachment,
    ensure_user_identifier,
    list_attachments_metadata,
    paginate_messages,
)

logger = structlog.get_logger(__name__)


class OutlookClient:
    """Client for interacting with Microsoft Graph API for Outlook."""

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        tenant_id: str | None = None,
        recipient_email: str | None = None,
    ):
        self.client_id = client_id or settings.outlook_client_id
        self.client_secret = client_secret or settings.outlook_client_secret
        self.tenant_id = tenant_id or settings.outlook_tenant_id
        self.recipient_email = recipient_email or settings.outlook_recipient_email
        self.timeout = settings.outlook_api_timeout

        if not all([self.client_id, self.client_secret, self.tenant_id]):
            raise ValueError(
                "Outlook credentials are required (client_id, client_secret, tenant_id)"
            )

        self.authority = f"https://login.microsoftonline.com/{self.tenant_id}"
        self.scope = ["https://graph.microsoft.com/.default"]
        self.graph_endpoint = "https://graph.microsoft.com/v1.0"

        self.app = ConfidentialClientApplication(
            client_id=self.client_id,
            client_credential=self.client_secret,
            authority=self.authority,
        )

        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None

    async def _get_access_token(self) -> str:
        """Get or refresh access token."""
        if self._access_token and self._token_expires_at:
            if datetime.utcnow() < (self._token_expires_at - timedelta(minutes=5)):
                return self._access_token

        loop = asyncio.get_event_loop()
        try:
            result_raw = await loop.run_in_executor(
                None,
                self.app.acquire_token_for_client,
                self.scope,
            )
        except Exception as e:
            logger.error("Token acquisition exception", error=str(e), exc_info=True)
            raise ValueError(f"Failed to acquire access token: {str(e)}") from e

        if not result_raw or not isinstance(result_raw, dict):
            raise ValueError("Token acquisition returned invalid result")

        result: dict[str, Any] = result_raw

        if "access_token" not in result:
            error_code = result.get("error", "unknown_error")
            error_description = result.get("error_description", "No error description provided")
            error_uri = result.get("error_uri")

            logger.error(
                "Token acquisition failed",
                error_code=error_code,
                error_description=error_description,
                error_uri=error_uri,
            )
            raise ValueError(f"Failed to acquire access token: {error_code} - {error_description}")

        access_token = result.get("access_token")
        if not access_token or not isinstance(access_token, str):
            raise ValueError("Invalid access token in response")

        self._access_token = access_token
        expires_in = result.get("expires_in", 3600)
        if not isinstance(expires_in, (int, float)):
            expires_in = 3600

        self._token_expires_at = datetime.utcnow() + timedelta(seconds=int(expires_in))
        logger.debug("Access token acquired", expires_in=int(expires_in))
        return self._access_token

    def _get_headers(self) -> dict[str, str]:
        """Get request headers with authorization."""
        if not self._access_token:
            raise ValueError("Access token not available. Call _get_access_token() first.")

        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

    async def _get_user_id(self) -> str:
        """Get user ID from email address, or fall back to email."""
        await self._get_access_token()
        headers = self._get_headers()

        url = f"{self.graph_endpoint}/users/{self.recipient_email}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                user_data = response.json()
                user_id = user_data.get("id")
                if user_id:
                    logger.debug("User ID resolved", email=self.recipient_email, user_id=user_id)
                    return user_id

        logger.warning(
            "Could not resolve user ID, using email as identifier", email=self.recipient_email
        )
        return self.recipient_email

    async def fetch_emails(
        self,
        subject_filter: list[str] | None = None,
        unread_only: bool = True,
        days_back: int = 7,
        max_results: int = 50,
    ) -> list[dict[str, Any]]:
        """Fetch emails from mailbox."""
        await self._get_access_token()
        user_identifier = await ensure_user_identifier(self)

        filter_query = build_message_filter(
            subject_filter=subject_filter,
            unread_only=unread_only,
            days_back=days_back,
        )

        url = f"{self.graph_endpoint}/users/{user_identifier}/messages"
        headers = self._get_headers()
        params: dict[str, Any] = {
            "$top": min(max_results, 50),
            "$orderby": "receivedDateTime desc",
            "$select": "id,subject,sender,receivedDateTime,conversationId,isRead,hasAttachments,bodyPreview,webLink",
        }
        if filter_query:
            params["$filter"] = filter_query

        logger.info(
            "Fetching emails from Graph API",
            user=user_identifier,
            url=url,
            params=params,
        )

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            emails = await paginate_messages(
                client_http=client,
                url=url,
                headers=headers,
                params=params,
                max_results=max_results,
                error_email=self.recipient_email,
            )

        if emails:
            sample_subjects = [e.get("subject", "")[:60] for e in emails[:5]]
            logger.info(
                "Emails fetched from Graph API",
                count=len(emails),
                sample_subjects=sample_subjects,
            )
        else:
            logger.warning("No emails fetched from Graph API", params=params)

        return emails

    async def iter_message_pages(
        self,
        *,
        days_back: int,
        page_size: int = 50,
        max_scan: int = 5000,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Walk a whole window using Graph's own ``@odata.nextLink`` cursor.

        ``fetch_emails`` cannot do this: it stops at ``max_results`` and discards
        the cursor, so every call returns the same newest page and nothing older
        is reachable.

        The cursor is Graph's, deliberately, rather than a ``receivedDateTime``
        bound computed here. A timestamp is not a unique key: when more messages
        share the oldest timestamp on a page than fit in one page, an exclusive
        bound skips the tied remainder and an inclusive bound returns the same
        page forever. Graph's own paging token has no such ambiguity, and a
        mailbox that takes a burst of same-second mail is exactly the busy
        mailbox this sweep matters for.

        Ordering is ``receivedDateTime asc`` — oldest first — and that direction
        is load-bearing when the guard trips. Newest-first, a window holding more
        than ``max_scan`` messages hands back the same newest page on every run
        and the tail is never reached by anything: it stays unscanned until it
        falls out of the window, starving exactly the oldest mail the sweep
        exists to recover. Oldest-first, the guard drops the *newest* instead,
        and those are reachable again on the next run as the older head of the
        window ages out — so an overflowing window drains rather than starves.
        (They are also the ones the live webhook path most likely just handled,
        and the ones ``mailbox_sweep_min_age_minutes`` defers anyway.)

        Returns ``(messages, scan_truncated)``. ``max_scan`` is a runaway guard,
        not a work cap — see the caller for why those must be different numbers.
        """
        await self._get_access_token()
        user_identifier = await ensure_user_identifier(self)

        url = f"{self.graph_endpoint}/users/{user_identifier}/messages"
        headers = self._get_headers()
        params: dict[str, Any] | None = {
            "$top": page_size,
            # Oldest first — see the docstring. Not a cosmetic difference: it is
            # what makes a truncated scan recoverable instead of permanent.
            "$orderby": "receivedDateTime asc",
            # Kept on ONE line, matching fetch_emails. tests/test_alert_enrichment.py
            # asserts line-by-line that every `$select` requests webLink — a select
            # that drops it makes the "Open email" button silently never appear —
            # and a wrapped string is invisible to that check.
            "$select": "id,subject,sender,receivedDateTime,conversationId,isRead,hasAttachments,bodyPreview,webLink",  # noqa: E501
        }
        filter_query = build_message_filter(
            subject_filter=None, unread_only=False, days_back=days_back
        )
        if filter_query and params is not None:
            params["$filter"] = filter_query

        collected: dict[str, dict[str, Any]] = {}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            while len(collected) < max_scan:
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()

                for message in data.get("value", []) or []:
                    if message.get("id"):
                        collected[message["id"]] = message

                next_link = data.get("@odata.nextLink")
                if not next_link:
                    return list(collected.values()), False
                # nextLink already carries every query parameter.
                url, params = next_link, None

        logger.warning(
            "Mailbox scan hit its runaway guard — the window is not fully read",
            scanned=len(collected),
            max_scan=max_scan,
            days_back=days_back,
        )
        return list(collected.values()), True

    async def list_attachment_names(self, message_id: str) -> list[str]:
        """Return attachment filenames for ``message_id`` without downloading bytes.

        Single Graph call against ``/messages/{id}/attachments`` — used by the FC
        classifier hardening gate to verify an internal-sender follow-up actually
        carries ``FC-<HAWB>.pdf`` attachments before treating it as an FC email.
        """
        await self._get_access_token()
        user_identifier = await ensure_user_identifier(self)
        headers = self._get_headers()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            _, attachments = await list_attachments_metadata(
                client_http=client,
                base_url=self.graph_endpoint,
                user_identifier=user_identifier,
                message_id=message_id,
                headers=headers,
            )
        return [(a.get("name") or "") for a in attachments]

    async def list_attachments_meta(self, message_id: str) -> list[dict[str, Any]]:
        """Return attachment metadata (name, size, contentType) without downloading bytes.

        Single Graph call against ``/messages/{id}/attachments``. Used by the
        e-way ingest to pick only the small PDFs worth downloading (size + type
        are known from metadata, so large scans are never fetched).
        """
        await self._get_access_token()
        user_identifier = await ensure_user_identifier(self)
        headers = self._get_headers()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            _, attachments = await list_attachments_metadata(
                client_http=client,
                base_url=self.graph_endpoint,
                user_identifier=user_identifier,
                message_id=message_id,
                headers=headers,
            )
        return [
            {
                "name": a.get("name") or "",
                "size": a.get("size") or 0,
                "contentType": a.get("contentType") or "",
            }
            for a in attachments
        ]

    async def download_attachments(
        self,
        message_id: str,
        storage_path: str | Path,
        name_filter: Callable[[str], bool] | None = None,
    ) -> list[dict[str, Any]]:
        """Download attachments from an email.

        If ``name_filter`` is provided, only attachments whose filename satisfies
        the predicate are downloaded. Used by fc_ingest to fetch a single
        ``FC-<HAWB>.pdf`` per shipment instead of every PDF in the email.
        """
        await self._get_access_token()

        storage_path = Path(storage_path)
        storage_path.mkdir(parents=True, exist_ok=True)

        user_identifier = await ensure_user_identifier(self)
        headers = self._get_headers()

        downloaded: list[dict[str, Any]] = []

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            base_url, attachments = await list_attachments_metadata(
                client_http=client,
                base_url=self.graph_endpoint,
                user_identifier=user_identifier,
                message_id=message_id,
                headers=headers,
            )

            if name_filter is not None:
                attachments = [a for a in attachments if name_filter(a.get("name") or "")]

            for attachment in attachments:
                info = await download_single_attachment(
                    client_http=client,
                    base_url=base_url,
                    attachment=attachment,
                    headers=headers,
                    storage_path=storage_path,
                    message_id=message_id,
                )
                downloaded.append(info)

        logger.info("Attachments downloaded", message_id=message_id, count=len(downloaded))
        return downloaded

    async def get_email_by_conversation_id(
        self,
        conversation_id: str,
        days_back: int = 7,
    ) -> list[dict[str, Any]]:
        """Get all emails in a conversation thread.

        Uses a date-only $filter and matches conversationId in Python, because
        Graph API $filter on conversationId is unreliable when the ID contains
        special characters (+, =, /) that break OData parsing.
        """
        await self._get_access_token()
        user_identifier = await ensure_user_identifier(self)
        headers = self._get_headers()

        url = f"{self.graph_endpoint}/users/{user_identifier}/messages"
        from datetime import datetime, timedelta, timezone

        since_date = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime(
            "%Y-%m-%dT%H:%M:%S.0000000Z"
        )

        params: dict[str, Any] = {
            "$filter": f"receivedDateTime ge {since_date}",
            "$orderby": "receivedDateTime desc",
            "$select": "id,subject,sender,receivedDateTime,conversationId,isRead,hasAttachments,webLink",
            "$top": 100,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, headers=headers, params=params)
            if response.status_code == 400:
                # Date filter failed — fetch without any filter and match in memory
                logger.warning(
                    "Date filter failed, fetching without filter", conversation_id=conversation_id
                )
                params.pop("$filter", None)
                response = await client.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()

        all_messages = data.get("value", [])
        # Filter by conversationId in Python — avoids OData encoding issues
        emails = [m for m in all_messages if m.get("conversationId") == conversation_id]
        logger.info(
            "Conversation emails fetched", conversation_id=conversation_id, count=len(emails)
        )
        return emails

    async def get_message_detail(self, message_id: str) -> dict[str, Any]:
        """Fetch a single email message with body content."""
        await self._get_access_token()
        user_identifier = await ensure_user_identifier(self)
        headers = self._get_headers()

        encoded_id = quote(message_id, safe="")
        url = f"{self.graph_endpoint}/users/{user_identifier}/messages/{encoded_id}"
        params: dict[str, Any] = {
            "$select": "id,subject,sender,receivedDateTime,conversationId,body,bodyPreview,hasAttachments,webLink",
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            return data

    async def send_email(
        self,
        to: list[str],
        subject: str,
        body_html: str,
        from_address: str | None = None,
        attachment_paths: list[str] | None = None,
        cc: list[str] | None = None,  # Local change: the DO app copies every mail it sends.
    ) -> dict[str, Any]:
        """Send an email via Microsoft Graph and return message metadata.

        Uses the draft-then-send flow (POST /messages → POST /messages/{id}/send)
        so we get back the message id and conversationId — needed for reply tracking.

        When attachment_paths is provided the files are base64-encoded and attached
        as fileAttachment objects (Graph limit: 3 MB total for inline attachments).

        Returns:
            {"id": <message_id>, "conversationId": <conversation_id>}
        """
        import base64

        if not to:
            logger.warning("send_email called with empty recipient list — skipping")
            return {}

        await self._get_access_token()
        sender = from_address or self.recipient_email
        headers = self._get_headers()

        # Build message payload
        message: dict[str, Any] = {
            "subject": subject,
            "body": {"contentType": "HTML", "content": body_html},
            "toRecipients": [{"emailAddress": {"address": addr}} for addr in to],
        }
        # Local change: Cc, for the DO app's mail_cc.
        if cc:
            message["ccRecipients"] = [{"emailAddress": {"address": addr}} for addr in cc]

        # Encode attachments if provided
        if attachment_paths:
            attachments = []
            for path in attachment_paths:
                file = Path(path)
                if not file.exists():
                    raise FileNotFoundError(
                        f"Attachment file not found on disk — cannot send email without it. "
                        f"path={path!r}"
                    )
                import mimetypes

                content_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
                content_bytes = base64.b64encode(file.read_bytes()).decode("utf-8")
                attachments.append(
                    {
                        "@odata.type": "#microsoft.graph.fileAttachment",
                        "name": file.name,
                        "contentType": content_type,
                        "contentBytes": content_bytes,
                    }
                )
            if attachments:
                message["attachments"] = attachments

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            # Step 1: create draft → get message id + conversationId
            draft_url = f"{self.graph_endpoint}/users/{sender}/messages"
            draft_resp = await client.post(draft_url, headers=headers, json=message)
            if draft_resp.status_code not in (200, 201):
                logger.error(
                    "Failed to create draft email",
                    http_status=draft_resp.status_code,
                    error_code=error_code_from_response(draft_resp),
                )
                draft_resp.raise_for_status()

            draft = draft_resp.json()
            message_id: str = draft.get("id", "")
            conversation_id: str = draft.get("conversationId", "")

            # Step 2: send the draft
            send_url = f"{self.graph_endpoint}/users/{sender}/messages/{message_id}/send"
            send_resp = await client.post(send_url, headers=headers)
            if send_resp.status_code == 202:
                logger.info(
                    "Email sent",
                    to=to,
                    subject=subject[:80],
                    message_id=message_id,
                    conversation_id=conversation_id,
                )
            else:
                logger.error(
                    "Failed to send email",
                    http_status=send_resp.status_code,
                    error_code=error_code_from_response(send_resp),
                    to=to,
                    subject=subject[:80],
                )
                send_resp.raise_for_status()

        return {"id": message_id, "conversationId": conversation_id}

    async def reply_to_message(
        self,
        message_id: str,
        body_html: str,
        from_address: str | None = None,
    ) -> dict[str, Any]:
        """Reply to an existing message on the same thread via Microsoft Graph.

        Uses createReply (draft) → send flow so the reply stays in the same
        conversation thread. Returns {"id": <new_message_id>, "conversationId": ...}.
        """
        await self._get_access_token()
        sender = from_address or self.recipient_email
        headers = self._get_headers()

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            # Step 1: create a reply draft
            draft_url = f"{self.graph_endpoint}/users/{sender}/messages/{message_id}/createReply"
            draft_resp = await client.post(draft_url, headers=headers, json={})
            if draft_resp.status_code not in (200, 201):
                logger.error(
                    "Failed to create reply draft",
                    http_status=draft_resp.status_code,
                    error_code=error_code_from_response(draft_resp),
                )
                draft_resp.raise_for_status()

            draft = draft_resp.json()
            draft_id: str = draft.get("id", "")
            conversation_id: str = draft.get("conversationId", "")

            # Step 2: update body of the draft
            update_url = f"{self.graph_endpoint}/users/{sender}/messages/{draft_id}"
            update_resp = await client.patch(
                update_url,
                headers=headers,
                json={"body": {"contentType": "HTML", "content": body_html}},
            )
            update_resp.raise_for_status()

            # Step 3: send the draft
            send_url = f"{self.graph_endpoint}/users/{sender}/messages/{draft_id}/send"
            send_resp = await client.post(send_url, headers=headers)
            if send_resp.status_code not in (202, 204):
                logger.error(
                    "Failed to send reply",
                    http_status=send_resp.status_code,
                    error_code=error_code_from_response(send_resp),
                )
                send_resp.raise_for_status()

        logger.info(
            "Reply sent on thread",
            original_message_id=message_id,
            draft_id=draft_id,
            conversation_id=conversation_id,
        )
        return {"id": draft_id, "conversationId": conversation_id}
