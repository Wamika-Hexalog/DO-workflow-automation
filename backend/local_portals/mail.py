"""Carries the local CMA CGM's mail to the app the way real mail arrives: into the app's
mailbox on this machine (the folder scripts/simulate_mail.py fills), then a Graph-style
notice to the app's webhook, so the app reads it like any other mail. It also plays
Finance: it answers the app's payment mail with the two UTRs.

Off unless DO_PORTALS_MAIL_TO names the app (make demo-local sets it), so tests and a
plain `make portals` never reach an app.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import Settings

from .store import Store, now

logger = logging.getLogger(__name__)


def app_url() -> str:
    return os.getenv("DO_PORTALS_MAIL_TO", "").rstrip("/")


def _deliver(settings: Settings, subject: str, body: str, attachments: list[Path]) -> str:
    """Puts one mail in the app's mailbox and tells the app's webhook, as Graph does."""
    message_id = secrets.token_hex(16)
    folder = settings.storage_dir / "inbox" / message_id
    (folder / "attachments").mkdir(parents=True)
    for path in attachments:
        shutil.copy(path, folder / "attachments" / path.name)
    message = {
        "id": message_id,
        "subject": f"{settings.mail_marker} {subject}".strip(),
        "sender": {"emailAddress": {"address": "noreply@local-portals"}},
        "body": {"contentType": "text", "content": body},
        "hasAttachments": bool(attachments),
    }
    (folder / "message.json").write_text(json.dumps(message, indent=2))
    notice = {
        "value": [{
            "changeType": "created", "clientState": settings.graph_client_state,
            "resource": f"Users/local/Messages('{message_id}')", "resourceData": {"id": message_id},
        }]
    }
    request = urllib.request.Request(
        f"{app_url()}/api/webhooks/microsoft-graph", data=json.dumps(notice).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    urllib.request.urlopen(request, timeout=10).close()
    return message_id


def deliver_pending(store: Store) -> int:
    """Delivers each outbox mail the app hasn't had yet. Returns how many went."""
    if not app_url():
        return 0
    settings = Settings.from_env()
    with store.read() as data:
        pending = [dict(m) for m in data["outbox"] if not m.get("delivered")]
    sent = 0
    for mail in reversed(pending):  # the outbox is newest first
        codes = [f"{settings.inject_code_prefix}:{':'.join(part for part in code if part)}" for code in mail.get("codes", [])]
        body = "\n".join([mail["body"], *codes])
        with tempfile.TemporaryDirectory() as tmp:
            attachments = []
            for found in (store.file(f) for f in mail["attachments"]):
                if found is not None:
                    name, path = found
                    attachments.append(Path(shutil.copyfile(path, Path(tmp) / name)))  # the app routes a file by its name
            try:
                _deliver(settings, mail["subject"], body, attachments)
            except OSError:
                logger.exception("Could not hand %r to the app at %s", mail["subject"], app_url())
                continue
        with store.change() as data:
            for item in data["outbox"]:
                if item["id"] == mail["id"]:
                    item["delivered"] = now()
        sent += 1
    return sent


# Finance -------------------------------------------------------------------------


def _addresses(message: dict[str, Any], key: str) -> str:
    return ", ".join(r["emailAddress"]["address"] for r in message.get(key, []))


def finance_mails(store: Store) -> list[dict[str, Any]]:
    """The app's payment mails (its outbox), with Finance's reply if it has sent one."""
    if not app_url():
        return []
    outbox = Settings.from_env().storage_dir / "outbox"
    with store.read() as data:
        replied = dict(data.get("finance", {}))
    mails = []
    for path in sorted(outbox.glob("*/message.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        message = json.loads(path.read_text())
        mails.append({"id": message["id"], "to": _addresses(message, "toRecipients"), "cc": _addresses(message, "ccRecipients"),
                      "subject": message["subject"], "at": path.stat().st_mtime,
                      "reply": replied.get(message["id"])})
    return mails


def _utr() -> str:
    return f"{secrets.randbelow(10**12):012d}"


def pay(store: Store, message_id: str) -> str:
    """Finance pays CMA CGM and replies on the app's mail with the two UTRs."""
    settings = Settings.from_env()
    if not re.fullmatch(r"[0-9a-f]{32}", message_id):
        return "No such mail."
    path = settings.storage_dir / "outbox" / message_id / "message.json"
    if not path.is_file():
        return "No such mail."
    with store.read() as data:
        if message_id in data.get("finance", {}):
            return "Finance has already replied to that mail."
    message = json.loads(path.read_text())
    invoice_utr, deposit_utr = _utr(), _utr()
    quoted = re.sub(r"<[^>]+>", "\n", message["body"]["content"])
    body = (
        f"Paid today.\nInvoice UTR: {invoice_utr}\nDeposit UTR: {deposit_utr}\n\n"
        f"On {datetime.now():%d %b %Y}, the DO workflow wrote:\n{quoted}"
    )
    subject = "Re: " + message["subject"].replace(settings.mail_marker, "").strip()
    _deliver(settings, subject, body, [])
    with store.change() as data:
        data.setdefault("finance", {})[message_id] = {"at": now(), "invoice_utr": invoice_utr, "deposit_utr": deposit_utr}
    return f"Finance paid and replied: invoice UTR {invoice_utr}, deposit UTR {deposit_utr}."


def unanswered_finance_mails(store: Store) -> list[str]:
    return [m["id"] for m in finance_mails(store) if not m["reply"]]
