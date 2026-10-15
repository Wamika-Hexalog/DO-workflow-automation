"""The DO invoice: request it on ODeX once the MBL is in, collect it once the liner's
mail says it's released, then mail it to Finance. Each runs by itself; ops can start
one again after it failed.

    customs/do.document.received[mbl] → do-invoice-request → customs/do.invoice.requested
    customs/do.invoice.verified ──────→ do-invoice-collect → customs/do.invoice.collected
    customs/do.invoice.collected ─────→ do-finance-mail ───→ customs/do.payment.requested
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import inngest

from .. import claims, portal_params, process, tasks
from ..connectors.file_mailbox import FileMailbox
from ..connectors.outlook import OutlookClient
from ..runtime import Runtime
from .events import EventNames
from .portal import ODEX_BROWSER, check_portal_after, run_step
from .steps import fail_task_handler, job, shipment_id_of


def _retry(task_key: str) -> inngest.TriggerEvent:
    return inngest.TriggerEvent(event=EventNames.TASK_COMPLETED, expression=f'event.data.task_key == "{task_key}"')


def create_invoice_request_function(rt: Runtime) -> inngest.Function:
    step_key = portal_params.REQUEST_INVOICE

    @rt.inngest.create_function(
        fn_id="do-invoice-request",
        name="Invoice: request it on ODeX",
        trigger=[
            inngest.TriggerEvent(event=EventNames.DOCUMENT_RECEIVED, expression='event.data.slot_id == "mbl"'),
            _retry(tasks.ODEX_INVOICE_REQUEST),
        ],
        concurrency=ODEX_BROWSER,
        retries=0,  # it submits: a failure goes to ops, who check ODeX first
        on_failure=fail_task_handler(rt, tasks.ODEX_INVOICE_REQUEST, after=check_portal_after(step_key)),
    )
    async def request(ctx: inngest.Context) -> dict:
        shipment_id = shipment_id_of(ctx)
        retry = ctx.event.name == EventNames.TASK_COMPLETED
        start = await ctx.step.run("start", job(process.start_job, rt, shipment_id, tasks.ODEX_INVOICE_REQUEST, retry))
        if not start["run"]:
            return start
        params = await ctx.step.run("params", job(portal_params.params_job, rt, shipment_id, step_key))
        readings = {}
        for target, values in params.items():
            readings[target] = await ctx.step.run(
                f"request-on-odex:{target}", run_step(rt, shipment_id, step_key, target, values, submits=True)
            )
        result = await ctx.step.run("record", job(process.record_job, rt, shipment_id, step_key, readings))
        await ctx.step.send_event(
            "invoice-requested", inngest.Event(name=EventNames.INVOICE_REQUESTED, data={"shipment_id": shipment_id})
        )
        return result

    return request


def create_invoice_collect_function(rt: Runtime) -> inngest.Function:
    step_key = portal_params.INVOICE_FETCH

    @rt.inngest.create_function(
        fn_id="do-invoice-collect",
        name="Invoice: collect it from ODeX",
        trigger=[inngest.TriggerEvent(event=EventNames.INVOICE_VERIFIED), _retry(tasks.ODEX_INVOICE_COLLECT)],
        concurrency=ODEX_BROWSER,
        retries=1,  # it only reads
        on_failure=fail_task_handler(rt, tasks.ODEX_INVOICE_COLLECT),
    )
    async def collect(ctx: inngest.Context) -> dict:
        shipment_id = shipment_id_of(ctx)
        retry = ctx.event.name == EventNames.TASK_COMPLETED
        start = await ctx.step.run("start", job(process.start_job, rt, shipment_id, tasks.ODEX_INVOICE_COLLECT, retry))
        if not start["run"]:
            return start
        params = await ctx.step.run("params", job(portal_params.params_job, rt, shipment_id, step_key))
        readings = {}
        for target, values in params.items():
            readings[target] = await ctx.step.run(
                f"collect-from-odex:{target}", run_step(rt, shipment_id, step_key, target, values, submits=False)
            )
        result = await ctx.step.run("record", job(process.record_job, rt, shipment_id, step_key, readings))
        if result["finished"]:
            await ctx.step.send_event(
                "invoice-collected", inngest.Event(name=EventNames.INVOICE_COLLECTED, data={"shipment_id": shipment_id})
            )
        return result

    return collect


def make_sender(rt: Runtime) -> OutlookClient | FileMailbox:
    """The mailbox the app sends from: Outlook when it is set up, else the outbox on disk."""
    settings = rt.settings
    if not settings.outlook_client_id:
        return FileMailbox(settings.storage_dir / "outbox")
    return OutlookClient(
        client_id=settings.outlook_client_id,
        client_secret=settings.outlook_client_secret,
        tenant_id=settings.outlook_tenant_id,
        recipient_email=settings.outlook_recipient_email,
    )


def _send_once(rt: Runtime, shipment_id: str, mail: dict[str, Any]) -> Callable[[], Awaitable[dict[str, Any]]]:
    """Step handler: sends the mail to Finance, never twice for the same job."""
    key = f"finance_mail:{shipment_id}"

    def take() -> dict[str, Any] | None:
        with rt.sessionmaker() as session:
            return claims.take(session, key, shipment_id)

    def settle(result: dict[str, Any] | None, note: str = "") -> None:
        with rt.sessionmaker() as session:
            if result is None:
                claims.fail(session, key, note)
            else:
                claims.finish(session, key, result)

    async def send() -> dict[str, Any]:
        try:
            earlier = await asyncio.to_thread(take)
        except claims.InFlight:
            raise inngest.NonRetriableError(
                "Another run is mailing Finance, or one stopped part way. Check the sent mail before trying again."
            ) from None
        if earlier is not None:
            return earlier
        try:
            message = await make_sender(rt).send_email(mail["to"], mail["subject"], mail["body_html"],
                                                       attachment_paths=mail["attachments"], cc=mail.get("cc"))
        except Exception as exc:
            await asyncio.to_thread(settle, None, str(exc))
            raise inngest.NonRetriableError(f"The mail to Finance wasn't sent: {exc}") from exc
        sent = {"id": (message or {}).get("id"), "to": mail["to"], "cc": mail.get("cc") or []}
        await asyncio.to_thread(settle, sent)
        return sent

    return send


def create_finance_mail_function(rt: Runtime) -> inngest.Function:
    @rt.inngest.create_function(
        fn_id="do-finance-mail",
        name="Invoice: mail it to Finance",
        trigger=[inngest.TriggerEvent(event=EventNames.INVOICE_COLLECTED), _retry(tasks.FINANCE_MAIL)],
        retries=0,  # it sends mail: a failure goes to ops
        on_failure=fail_task_handler(rt, tasks.FINANCE_MAIL),
    )
    async def mail_finance(ctx: inngest.Context) -> dict:
        shipment_id = shipment_id_of(ctx)
        retry = ctx.event.name == EventNames.TASK_COMPLETED
        start = await ctx.step.run("start", job(process.start_job, rt, shipment_id, tasks.FINANCE_MAIL, retry))
        if not start["run"]:
            return start
        mail = await ctx.step.run("write-mail", job(process.finance_mail_job, rt, shipment_id))
        sent = await ctx.step.run("send-mail", _send_once(rt, shipment_id, mail))
        await ctx.step.run("record", job(process.record_finance_mail_job, rt, shipment_id, sent))
        await ctx.step.send_event(
            "payment-requested", inngest.Event(name=EventNames.PAYMENT_REQUESTED, data={"shipment_id": shipment_id})
        )
        return sent

    return mail_finance
