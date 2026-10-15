"""The payments: confirm each on CMA CGM's RTGS page, then raise the security deposit
on ODeX. Each waits for ops to approve it, because it confirms or moves money; the
tasks come up once the job has what each needs.

    customs/do.shipment.updated, customs/do.document.received,
    customs/do.payment.utr-received, customs/do.invoice.collected
        → do-payments-refresh → tasks ready for ops; customs/do.deposit.approved
          once ODeX's security slip is in for every house bill
    customs/task.completed[cma_rtgs_payment] → do-rtgs-payment ─┐
    customs/task.completed[cma_rtgs_deposit] → do-rtgs-deposit ─┴→ customs/do.rtgs.submitted
    customs/task.completed[odex_deposit] → do-deposit-request → customs/do.deposit.submitted
"""
from __future__ import annotations

from datetime import timedelta

import inngest

from .. import portal_params, process, tasks
from ..runtime import Runtime
from .events import EventNames
from .portal import ODEX_BROWSER, RTGS_BROWSER, check_portal_after, run_step
from .steps import fail_task_handler, job, shipment_id_of


def create_payments_refresh_function(rt: Runtime) -> inngest.Function:
    @rt.inngest.create_function(
        fn_id="do-payments-refresh",
        name="Payments: put them in front of ops",
        trigger=[
            inngest.TriggerEvent(event=EventNames.SHIPMENT_UPDATED),
            inngest.TriggerEvent(event=EventNames.DOCUMENT_RECEIVED),
            inngest.TriggerEvent(event=EventNames.UTR_RECEIVED),
            inngest.TriggerEvent(event=EventNames.INVOICE_COLLECTED),
        ],
        debounce=inngest.Debounce(period=timedelta(seconds=2), key="event.data.shipment_id"),
        retries=2,
    )
    async def refresh(ctx: inngest.Context) -> dict:
        shipment_id = shipment_id_of(ctx)
        result = await ctx.step.run("refresh", job(process.refresh_payments_job, rt, shipment_id))
        if result["deposit_approved"]:
            await ctx.step.send_event(
                "deposit-approved", inngest.Event(name=EventNames.DEPOSIT_APPROVED, data={"shipment_id": shipment_id})
            )
        return result

    return refresh


def _approved(task_key: str) -> inngest.TriggerEvent:
    return inngest.TriggerEvent(event=EventNames.TASK_COMPLETED, expression=f'event.data.task_key == "{task_key}"')


def _create_rtgs_function(rt: Runtime, step_key: str, fn_id: str, name: str) -> inngest.Function:
    task_key = process.TASK_OF[step_key]

    @rt.inngest.create_function(
        fn_id=fn_id,
        name=name,
        trigger=_approved(task_key),
        concurrency=RTGS_BROWSER,
        retries=0,  # it confirms a payment: a failure goes to ops, who check the RTGS page first
        on_failure=fail_task_handler(rt, task_key, after=check_portal_after(step_key)),
    )
    async def confirm(ctx: inngest.Context) -> dict:
        shipment_id = shipment_id_of(ctx)
        params = await ctx.step.run("params", job(portal_params.params_job, rt, shipment_id, step_key))
        readings = {}
        for target, values in params.items():
            readings[target] = await ctx.step.run(
                f"confirm-on-rtgs:{target}", run_step(rt, shipment_id, step_key, target, values, submits=True)
            )
        result = await ctx.step.run("record", job(process.record_job, rt, shipment_id, step_key, readings))
        await ctx.step.send_event(
            "rtgs-submitted",
            inngest.Event(name=EventNames.RTGS_SUBMITTED, data={"shipment_id": shipment_id, "step": step_key}),
        )
        return result

    return confirm


def create_rtgs_payment_function(rt: Runtime) -> inngest.Function:
    return _create_rtgs_function(rt, portal_params.RTGS_SHIPMENT, "do-rtgs-payment", "Payments: confirm the shipment payment")


def create_rtgs_deposit_function(rt: Runtime) -> inngest.Function:
    return _create_rtgs_function(rt, portal_params.RTGS_DEPOSIT, "do-rtgs-deposit", "Payments: confirm the security deposit")


def create_deposit_request_function(rt: Runtime) -> inngest.Function:
    step_key = portal_params.DEPOSIT

    @rt.inngest.create_function(
        fn_id="do-deposit-request",
        name="Deposit: raise it on ODeX",
        trigger=_approved(tasks.ODEX_DEPOSIT),
        concurrency=ODEX_BROWSER,
        retries=0,  # it moves money: a failure goes to ops, who check ODeX first
        on_failure=fail_task_handler(rt, tasks.ODEX_DEPOSIT, after=check_portal_after(step_key)),
    )
    async def request(ctx: inngest.Context) -> dict:
        shipment_id = shipment_id_of(ctx)
        params = await ctx.step.run("params", job(portal_params.params_job, rt, shipment_id, step_key))
        readings = {}
        for hbl, values in params.items():
            readings[hbl] = await ctx.step.run(
                f"deposit-on-odex:{hbl}", run_step(rt, shipment_id, step_key, hbl, values, submits=True)
            )
        result = await ctx.step.run("record", job(process.record_job, rt, shipment_id, step_key, readings))
        await ctx.step.send_event(
            "deposit-submitted", inngest.Event(name=EventNames.DEPOSIT_SUBMITTED, data={"shipment_id": shipment_id})
        )
        return result

    return request
