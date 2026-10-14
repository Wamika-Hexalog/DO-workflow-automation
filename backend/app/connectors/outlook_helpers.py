# copied from python-brain @ 4bb5af8; imports changed to this package, nothing else
"""Helper functions for OutlookClient to keep methods small and focused."""

import hashlib
import re
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, TYPE_CHECKING
from urllib.parse import quote

import httpx
import structlog

if TYPE_CHECKING:  # pragma: no cover
    from .outlook import OutlookClient

logger = structlog.get_logger(__name__)


# ── Attachment path safety (HEX-2471) ─────────────────────────────────────────
#
# Attachments used to be written to `storage_path / attachment["name"]`, with the
# name taken verbatim from Graph and the directory supplied by the caller. Two
# separate problems, one line apart:
#
#   * the name is attacker-influenced text used as a path, so `../../x` escapes
#     the storage root,
#   * the directory did not distinguish one attachment from another, so any two
#     files sharing a name overwrote each other — and the one that lost is simply
#     gone, with a Document row still pointing at the winner's bytes. Two
#     forwarders' `HAWB.pdf` is the common case, and it is a candidate mechanism
#     for the open CMC HAWB-mapping bug.
#
# The fix lives here rather than in each caller because every caller had the
# problem and only some had noticed: eway_part_a scoped by message id after
# losing slips to it, ooc scoped by BOE number, and cipl scoped by a 16-character
# prefix of the message id — which does nothing at all, because the first ~30
# characters of an Outlook id are the mailbox/folder GUID and are identical for
# every message in the folder (pinned by tests/test_log_context.py). Doing it at
# the write means the two writers that passed the bare storage root
# (pre_alert_steps, fc_ingest) are fixed by the same change, and a future caller
# cannot forget.

# A denylist, not an allowlist, and that choice is load-bearing. The on-disk
# basename reaches ops: `document_upload.py:255` sends `path.name` as the
# multipart filename, so it is what a document is called in ShipThis. An
# allowlist of `[A-Za-z0-9._-]` closes the traversal and also rewrites every
# ordinary name a forwarder sends — `HAWB ML-260513D (1).pdf` would arrive in the
# ERP as `HAWB_ML-260513D__1_.pdf`, and a Chinese or accented filename would be
# reduced to underscores. That is a business-visible change with no security
# value: spaces, parentheses and non-ASCII letters cannot escape a directory.
#
# So strip only what is actually dangerous — path separators, the NUL and control
# bytes, and the Windows-reserved punctuation — and leave the name otherwise as
# the sender wrote it.
_UNSAFE_NAME_RE = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f]+')
# Comfortably inside the 255-byte limit every relevant filesystem shares, with
# room for the separators and the digest directories around it.
_MAX_NAME_LEN = 120


def safe_name(filename: str | None, *, fallback: str = "attachment") -> str:
    """A basename that cannot escape the directory it is joined to.

    The extension is preserved deliberately — routing decisions downstream are
    made on it (`_is_supported_extend_attachment`, the `.pdf` filters), so a
    sanitiser that dropped it would quietly change which pipeline a document
    enters.

    Note this sanitises the **path only**. The original Graph name is still what
    is stored on `Document.filename` and still what `name_filter` predicates see,
    because FC/HAWB filename matching and the `(email_id, filename)` dedupe key
    are both built on it. The vulnerability is the write, not the metadata.
    """
    raw = (filename or "").strip()
    # Both separators, always: a POSIX host still has to reject `..\..\x`, which
    # PurePosixPath treats as one ordinary filename.
    raw = PureWindowsPath(PurePosixPath(raw).name).name
    cleaned = _UNSAFE_NAME_RE.sub("_", raw)
    # Leading dots would make the file hidden, and `.`/`..` are traversal even
    # after the basename step. Trailing dots and spaces are stripped because
    # Windows silently drops them, which would make two names collide there.
    cleaned = cleaned.lstrip(". ").rstrip(". ")
    if not cleaned:
        return fallback
    if len(cleaned) > _MAX_NAME_LEN:
        stem, dot, ext = cleaned.rpartition(".")
        if dot and len(ext) <= 10:
            cleaned = stem[: _MAX_NAME_LEN - len(ext) - 1] + "." + ext
        else:
            cleaned = cleaned[:_MAX_NAME_LEN]
    return cleaned


def attachment_dir(storage_path: Path, message_id: str, attachment_id: str) -> Path:
    """A directory unique to one attachment of one message.

    Both ids, not just the attachment id: Graph guarantees the attachment id is
    unique within its message, not across the mailbox. Digested rather than used
    raw because both are long base64-ish strings containing `/` and `+`, and the
    whole point here is not to put untrusted text in a path.

    Digest the **whole** message id, never a prefix — see the module comment on
    why prefixing an Outlook id is not scoping.
    """
    msg = hashlib.sha256((message_id or "").encode()).hexdigest()[:24]
    att = hashlib.sha256((attachment_id or "").encode()).hexdigest()[:16]
    return storage_path / msg / att


async def ensure_user_identifier(client: "OutlookClient") -> str:
    """Resolve user identifier (user id or email) with fallback."""
    try:
        return await client._get_user_id()  # noqa: SLF001 - internal helper by design
    except Exception:
        logger.warning(
            "Falling back to email as user identifier",
            email=client.recipient_email,
        )
        return client.recipient_email


def build_message_filter(
    subject_filter: list[str] | None,
    unread_only: bool,
    days_back: int,
) -> str | None:
    """Build OData $filter string for messages."""
    filters: list[str] = []

    if unread_only:
        filters.append("isRead eq false")

    since_date = (datetime.utcnow() - timedelta(days=days_back)).isoformat() + "Z"
    filters.append(f"receivedDateTime ge {since_date}")

    logger.info(
        "Building message filter",
        unread_only=unread_only,
        days_back=days_back,
        since_date=since_date,
        subject_filter=subject_filter,
    )

    if subject_filter:
        # Use simple contains(subject, ...) to avoid complex function calls that can break OData parsing
        subject_conditions = [f"contains(subject, '{kw}')" for kw in subject_filter]
        if subject_conditions:
            filters.append(f"({' or '.join(subject_conditions)})")

    filter_string = " and ".join(filters) if filters else None
    logger.debug("OData filter built", filter=filter_string)
    return filter_string


async def paginate_messages(
    client_http: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    params: dict[str, Any],
    max_results: int,
    error_email: str,
) -> list[dict[str, Any]]:
    """Fetch messages with pagination until max_results."""
    emails: list[dict[str, Any]] = []

    while len(emails) < max_results:
        response = await client_http.get(url, headers=headers, params=params)

        if response.status_code == 404:
            logger.error(
                "User not found or not accessible",
                email=error_email,
                status_code=404,
                error_detail=response.text,
            )
            raise ValueError(
                f"User '{error_email}' not found or not accessible. "
                f"Check: 1) User exists in Azure AD, 2) Application has 'Mail.Read' permission, "
                f"3) Admin consent granted. Error: {response.text}"
            )

        response.raise_for_status()
        data = response.json()
        emails.extend(data.get("value", []))

        next_link = data.get("@odata.nextLink")
        if not next_link or len(emails) >= max_results:
            break

        url = next_link
        params = {}  # nextLink already has params

    return emails[:max_results]


async def list_attachments_metadata(
    client_http: httpx.AsyncClient,
    base_url: str,
    user_identifier: str,
    message_id: str,
    headers: dict[str, str],
) -> tuple[str, list[dict[str, Any]]]:
    """Return attachment endpoint URL and metadata list."""
    encoded_id = quote(message_id, safe="")
    url = f"{base_url}/users/{user_identifier}/messages/{encoded_id}/attachments"
    response = await client_http.get(url, headers=headers)
    response.raise_for_status()
    data = response.json()
    return url, data.get("value", [])


async def download_single_attachment(
    client_http: httpx.AsyncClient,
    base_url: str,
    attachment: dict[str, Any],
    headers: dict[str, str],
    storage_path: Path,
    message_id: str,
) -> dict[str, Any]:
    """Download one attachment and save it to disk."""
    attachment_id = attachment["id"]
    filename = attachment["name"]
    mime_type = attachment.get("contentType", "application/octet-stream")
    size = attachment.get("size", 0)

    download_url = f"{base_url}/{attachment_id}/$value"
    download_response = await client_http.get(download_url, headers=headers)
    download_response.raise_for_status()

    # Scoped and sanitised — see the module comment. `filename` below is still
    # the original Graph name: only the path is derived from the safe form.
    target_dir = attachment_dir(storage_path, message_id, attachment_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / safe_name(filename)
    file_path.write_bytes(download_response.content)

    logger.debug(
        "Attachment downloaded",
        message_id=message_id,
        filename=filename,
        size=size,
    )

    return {
        "name": filename,
        "path": str(file_path),
        "size": size,
        "mimeType": mime_type,
    }


def build_conversation_filter(conversation_id: str, days_back: int) -> tuple[str, str]:
    """Build OData filter for conversation ID (with and without date)."""
    encoded_conv_id = conversation_id.replace("'", "''")
    since_date = (datetime.utcnow() - timedelta(days=days_back)).isoformat() + "Z"
    primary = f"conversationId eq '{encoded_conv_id}' and receivedDateTime ge {since_date}"
    fallback = f"conversationId eq '{encoded_conv_id}'"
    return primary, fallback
