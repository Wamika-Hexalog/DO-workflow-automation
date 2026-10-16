"""Mail that carries injected codes (app/email_code.py) becomes DO events. One mail may carry
several lines, one per fact; each line is handled in order.

    EmailService:InboundMessage → email-inbound → customs/do.document.received (one per file)
                                                 | customs/do.shipment.updated
                                                 | customs/do.invoice.verified
                                                 | customs/do.payment.utr-received
                                                 | customs/do.edo.received

Mail without a code is ignored. Every mail goes in the mail log (InboundMail) with what it
did, or why it did nothing, for the assistant (app/assistant.py).
"""
from __future__ import annotations

import asyncio
import re
import tempfile
from pathlib import Path
from typing import Any

import inngest

from .. import email_ai, email_ingest
from ..config import Settings
from ..connectors.file_mailbox import FileMailbox
from ..connectors.outlook import OutlookClient
from ..email_code import InjectedCode, fields_for, parse_injected_codes
from ..email_infer import infer_codes, marker_present
from ..runtime import Runtime
from .events import INGEST_IDEMPOTENCY, EventNames
from .steps import failure_details, job

STAGE_EVENTS = {
    "invoice": EventNames.INVOICE_VERIFIED,
    "utr": EventNames.UTR_RECEIVED,
    "edo": EventNames.EDO_RECEIVED,
}


def make_outlook(settings: Settings, message_id: str) -> OutlookClient | FileMailbox:
    """Mail written by scripts/simulate_mail.py is read from disk; anything else comes from Graph."""
    local = settings.storage_dir / "inbox"
    simulated = re.fullmatch(r"[0-9a-f]{32}", message_id) is not None and (local / message_id).is_dir()
    if not settings.outlook_client_id or simulated:
        return FileMailbox(local)
    return OutlookClient(
        client_id=settings.outlook_client_id,
        client_secret=settings.outlook_client_secret,
        tenant_id=settings.outlook_tenant_id,
        recipient_email=settings.outlook_recipient_email,
    )


def _sender(msg: dict[str, Any]) -> str | None:
    return ((msg.get("sender") or {}).get("emailAddress") or {}).get("address")


def _fact(code: InjectedCode) -> str:
    """What a code tells, as the mail log says it. A UTR shows its last 4 digits only."""
    if code.stage == "utr":
        kind, _, utr = (code.arg or "").rpartition(":")
        return f"the {'deposit' if kind == 'deposit' else 'invoice'} UTR …{utr[-4:]}"
    if code.stage == "invoice":
        return f"invoice {code.arg}"
    if code.stage == "edo":
        return "the eDO"
    return "its file" if code.arg else "its files"


def create_email_inbound_function(rt: Runtime) -> inngest.Function:
    async def log_failure(ctx: inngest.Context) -> None:
        message_id = (((ctx.event.data or {}).get("event") or {}).get("data") or {}).get("message_id")
        if message_id:
            _, error = failure_details(ctx)
            await asyncio.to_thread(email_ingest.log_mail_job, rt, str(message_id), {"outcome": f"The app failed on it: {error}"})

    @rt.inngest.create_function(
        fn_id="email-inbound",
        name="Email: pick up mail with an injected code",
        trigger=inngest.TriggerEvent(event=EventNames.EMAIL_INBOUND),
        idempotency=INGEST_IDEMPOTENCY,
        retries=3,
        on_failure=log_failure,
    )
    async def inbound(ctx: inngest.Context) -> dict:
        message_id = str((ctx.event.data or {}).get("message_id") or "")
        if not message_id:
            raise inngest.NonRetriableError(f"{ctx.event.name} has no message_id.")

        async def fetch() -> dict[str, Any]:
            return await make_outlook(rt.settings, message_id).get_message_detail(message_id)

        msg = await ctx.step.run("fetch-message", fetch)
        body = (msg.get("body") or {}).get("content") or msg.get("bodyPreview") or ""
        subject = msg.get("subject") or ""
        marked = marker_present(rt.settings.mail_marker, subject, body)
        heard = {"sender": _sender(msg) or "", "subject": subject, "marked": marked}
        await ctx.step.run("log-mail-received", job(email_ingest.log_mail_job, rt, message_id, {**heard, "outcome": "Being read."}))

        async def logged(result: dict[str, Any], read_by: str, outcome: list[str], shipment_ids: list[str]) -> dict:
            row = {**heard, "read_by": read_by, "outcome": " ".join(outcome), "shipment_ids": list(dict.fromkeys(shipment_ids))}
            await ctx.step.run("log-mail", job(email_ingest.log_mail_job, rt, message_id, row))
            return result

        async def download() -> list[dict[str, Any]]:
            target = Path(tempfile.mkdtemp(prefix="do-inbox-"))
            return await make_outlook(rt.settings, message_id).download_attachments(message_id, target)

        files: list[dict[str, Any]] | None = None  # downloaded once: for the model, or for the first document code
        codes = parse_injected_codes(body, prefix=rt.settings.inject_code_prefix, subject=subject)
        read_by = "code" if codes else ""
        if not codes and marked:
            codes = infer_codes(subject, body, hbl_pattern=rt.settings.hbl_pattern)
            read_by = "rules" if codes else ""
        ai_note = ""
        if not codes and marked and rt.settings.google_api_key:
            # No rule knows this wording: the model reads it (app/email_ai.py).
            files = await ctx.step.run("download-attachments", download) if msg.get("hasAttachments") else []
            attachments = files

            async def read() -> dict[str, Any]:
                return await email_ai.read(rt.settings, subject, body, attachments)

            reading = await ctx.step.run("read-mail-with-ai", read)
            codes = [InjectedCode(**c) for c in reading["codes"]]
            ai_note, read_by = reading["note"], "ai"
            if not codes:
                result = {"routed": False, "reason": "no rule knew the mail, and the model found nothing to act on", "ai": reading}
                return await logged(result, read_by, [ai_note, "Nothing was done: the reading gave nothing to act on."], [])
        if not codes:
            why = (
                "Not read: no rule knows its wording, and the AI reader is off (GOOGLE_API_KEY is not set)."
                if marked
                else f"Ignored: it has neither the {rt.settings.mail_marker} marker nor a {rt.settings.inject_code_prefix} code."
            )
            return await logged({"routed": False, "reason": "no injected code"}, read_by, [why], [])

        next_file = 0  # a code that names a slot takes the next attachment
        results: list[dict[str, Any]] = []
        outcome = [ai_note] if ai_note else []
        jobs: list[str] = []
        noted: set[str] = set()  # the jobs whose history says the model read this mail
        for i, code in enumerate(codes):
            shipment_id = await ctx.step.run(f"find-shipment-{i}", job(email_ingest.find_shipment_job, rt, code.mbl_no))
            if shipment_id is None and i == 0 and code.stage == "document" and not code.arg:
                # The draft MBL/HBL mail is the first the system hears of a shipment: open its job.
                shipment_id = await ctx.step.run(f"create-shipment-{i}", job(email_ingest.create_shipment_job, rt, code.mbl_no))
                outcome.append(f"Opened a job for MBL {code.mbl_no}.")
            if shipment_id is None:
                results.append({"stage": code.stage, "routed": False, "reason": f"no shipment with MBL {code.mbl_no}"})
                outcome.append(
                    f"No job has B/L {code.mbl_no}, so {_fact(code)} went to no job. "
                    "Only the pre-alert (the draft MBL and HBL mail) opens a job."
                )
                continue
            jobs.append(shipment_id)
            if ai_note and shipment_id not in noted:
                noted.add(shipment_id)
                note = f"{ai_note} (from {_sender(msg)})"
                await ctx.step.run(f"note-ai-{i}", job(email_ingest.record_job, rt, shipment_id, "ai", note))
            if code.stage != "document":
                await _record_and_emit(ctx, rt, msg, message_id, code, shipment_id, i)
                results.append({"stage": code.stage, "routed": True})
                outcome.append(f"Recorded {_fact(code)} on the job of {code.mbl_no}.")
                continue

            if files is None:
                files = await ctx.step.run("download-attachments", download)
            if code.arg:  # a slot named in the code: the next attachment goes there
                if next_file >= len(files):
                    results.append({"stage": "document", "routed": False, "reason": "no attachment left for this code"})
                    outcome.append(f"No attachment was left for the code {code.arg}.")
                    continue
                f = files[next_file]
                next_file += 1
                one = await ctx.step.run(
                    f"attach-file-{i}", job(email_ingest.attach_file_job, rt, shipment_id, code.arg, f["path"], f["name"])
                )
                attached = [{**one, "slot_id": code.arg}] if one["attached"] else []
                filled: list[str] = one.get("filled", [])
                outcome.append(f"{f['name']} went to {code.arg}." if one["attached"] else f"{f['name']} was not attached: {one['reason']}.")
            else:  # MBL and HBL usually arrive together: each remaining file goes to its slot by file name
                routed = await ctx.step.run(
                    f"route-files-{i}", job(email_ingest.route_files_job, rt, shipment_id, files[next_file:])
                )
                attached, filled = [a for a in routed["attached"] if a["attached"]], routed["filled"]
                next_file = len(files)
                outcome.extend(
                    f"{a['name']} went to {a['slot_id']}." if a["attached"] else f"{a['name']} was not attached: {a['reason']}."
                    for a in routed["attached"]
                )
                outcome.extend(f"{s['name']} was skipped: {s['reason']}." for s in routed["skipped"])
                if not files:
                    outcome.append("The mail had no attachments.")
            if filled:
                outcome.append(f"Filled {', '.join(filled)}.")
            events = [
                inngest.Event(
                    name=EventNames.DOCUMENT_RECEIVED,
                    data={"shipment_id": shipment_id, "slot_id": a["slot_id"], "document_id": a["document_id"], "source": "email"},
                )
                for a in attached
            ]
            if filled:  # the folder check and the payments read the new numbers
                events.append(inngest.Event(name=EventNames.SHIPMENT_UPDATED, data={"shipment_id": shipment_id}))
            if events:
                await ctx.step.send_event(f"emit-do-events-{i}", events)
            results.append({"stage": "document", "routed": True, "attached": len(attached), "filled": filled})
        result = {"routed": any(r["routed"] for r in results), "read_by_ai": bool(ai_note), "results": results}
        return await logged(result, read_by, outcome, jobs)

    return inbound


async def _record_and_emit(
    ctx: inngest.Context, rt: Runtime, msg: dict[str, Any], message_id: str, code: InjectedCode, shipment_id: str, i: int
) -> None:
    sender = _sender(msg)
    detail = f"{code.stage} for MBL {code.mbl_no}" + (f": {code.arg}" if code.arg else "") + f" (from {sender})."
    await ctx.step.run(
        f"record-{i}", job(email_ingest.record_job, rt, shipment_id, code.stage, detail, fields_for(code))
    )
    data = {"shipment_id": shipment_id, "mbl_no": code.mbl_no, "arg": code.arg, "message_id": message_id, "from": sender}
    await ctx.step.send_event(f"emit-do-event-{i}", inngest.Event(name=STAGE_EVENTS[code.stage], data=data))
    if fields_for(code):  # the form fields changed
        await ctx.step.send_event(
            f"emit-updated-{i}", inngest.Event(name=EventNames.SHIPMENT_UPDATED, data={"shipment_id": shipment_id})
        )
