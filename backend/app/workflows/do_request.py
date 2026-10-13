"""The DO request on ODeX, one per house bill, after ops approve it.

    customs/task.completed[odex_do_request] → do-request-submit → customs/do.request.submitted

With an ODeX SOP the browser agent raises each request: it enters the HBL number
(ODeX wants the HBL, not the MBL) and uploads the folder PDF, the same file for
every house bill of the MBL. Without one, ops raise it by hand and their
approval records that it is done.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import inngest

from .. import claims, service, tasks
from ..db import IdempotencyKey
from ..runtime import Runtime
from . import browser
from .events import EventNames
from .steps import fail_task_handler, job, shipment_id_of

CLAIM = "do_request_submit"


def _claim_key(shipment_id: str, hbl: str) -> str:
    return f"{CLAIM}:{shipment_id}:{hbl}"


def _take(rt: Runtime, key: str, shipment_id: str) -> dict[str, Any] | None:
    with rt.sessionmaker() as session:
        return claims.take(session, key, shipment_id)


def _finish(rt: Runtime, key: str, result: dict[str, Any]) -> None:
    with rt.sessionmaker() as session:
        claims.finish(session, key, result)


def _fail(rt: Runtime, key: str, note: str) -> None:
    with rt.sessionmaker() as session:
        claims.fail(session, key, note)


def raise_on_odex(rt: Runtime, shipment_id: str, plan: dict[str, Any], hbl: str) -> Callable[[], Awaitable[dict]]:
    """Step handler: one ODeX DO request, never twice for the same house bill."""

    async def run() -> dict[str, Any]:
        key = _claim_key(shipment_id, hbl)
        try:
            earlier = await asyncio.to_thread(_take, rt, key, shipment_id)
        except claims.InFlight:
            raise inngest.NonRetriableError(
                f"Another run is raising the DO request for HBL {hbl}, or one stopped part way. "
                f"Check ODeX for HBL {hbl} before approving again."
            ) from None
        if earlier is not None:
            return {**earlier, "repeat": True}
        params = {
            "hbl_no": hbl,
            "mbl_no": plan["mbl_no"],
            "job_no": plan["job_no"],
            "folder_pdf_path": plan["folder_pdf"],
        }
        try:
            outcome = await browser.run_sop(
                rt,
                plan["sop"],
                params,
                f"ODeX DO request for HBL {hbl}",
                shipment_id=shipment_id,
                task_key=tasks.ODEX_DO_REQUEST,
            )
        except Exception as exc:
            await asyncio.to_thread(_fail, rt, key, str(exc))
            raise
        extracted = outcome.get("extraction_data") or {}
        result = {
            # The SOP's last extract step names the confirmation odex_request_ref.
            "reference": extracted.get("odex_request_ref"),
            "run_id": outcome.get("run_id"),
            "extracted": extracted,
        }
        await asyncio.to_thread(_finish, rt, key, result)
        return result

    return run


def release_claims(rt: Runtime, shipment_id: str, message: str) -> str:
    """After a failed run: free the claims it held, and tell ops which house bills
    to check on ODeX, because a request may have gone through before the failure."""
    with rt.sessionmaker() as session:
        claims.fail_open(session, shipment_id, f"{CLAIM}:{shipment_id}:", message)
        unsure = [
            row.key.rsplit(":", 1)[-1]
            for row in session.query(IdempotencyKey).filter(
                IdempotencyKey.shipment_id == shipment_id,
                IdempotencyKey.key.startswith(f"{CLAIM}:{shipment_id}:", autoescape=True),
                IdempotencyKey.status == claims.FAILED,
            )
        ]
    if not unsure:
        return message
    return f"{message} Check ODeX for HBL {', '.join(unsure)} before approving again: a request may have gone through."


def create_do_request_submit_function(rt: Runtime) -> inngest.Function:
    @rt.inngest.create_function(
        fn_id="do-request-submit",
        name="DO request: raise on ODeX",
        trigger=inngest.TriggerEvent(
            event=EventNames.TASK_COMPLETED, expression=f'event.data.task_key == "{tasks.ODEX_DO_REQUEST}"'
        ),
        # One ODeX browser session at a time across the environment (LLD §4.2).
        concurrency=[inngest.Concurrency(limit=1, scope="env", key='"odex-browser"')],
        # A browser run is not safe to repeat blindly: a failure goes to ops instead.
        retries=0,
        on_failure=fail_task_handler(rt, tasks.ODEX_DO_REQUEST, after=release_claims),
    )
    async def submit(ctx: inngest.Context) -> dict:
        shipment_id = shipment_id_of(ctx)
        by = str((ctx.event.data or {}).get("completed_by") or "ops")
        plan = await ctx.step.run("plan", job(service.do_request_plan_job, rt, shipment_id))
        results: dict[str, dict[str, Any]] = {}
        for hbl in plan["hbls"]:
            if plan["automated"]:
                results[hbl] = await ctx.step.run(f"odex-do-request:{hbl}", raise_on_odex(rt, shipment_id, plan, hbl))
            else:
                results[hbl] = {"by_hand": True}
        await ctx.step.run("record", job(service.record_do_request_job, rt, shipment_id, results, by))
        await ctx.step.send_event(
            "request-submitted",
            inngest.Event(name=EventNames.REQUEST_SUBMITTED, data={"shipment_id": shipment_id, "hbls": plan["hbls"]}),
        )
        return {"hbls": results}

    return submit
