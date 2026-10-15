"""Runs one portal step's SOP for one target, the MBL or a house bill. A step that
submits something takes an insert-once claim first, as the DO request does
(do_request.py), so a replayed run never submits twice; a step that only reads
runs again freely."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import inngest

from .. import claims, portal_params, process
from ..db import IdempotencyKey
from ..runtime import Runtime
from . import browser

# One ODeX session at a time across the environment, as for the DO request: ODeX
# signs out the other session of the same login.
ODEX_BROWSER = [inngest.Concurrency(limit=1, scope="env", key='"odex-browser"')]
RTGS_BROWSER = [inngest.Concurrency(limit=1, scope="env", key='"cma-rtgs-browser"')]


def _key(step_key: str, shipment_id: str, target: str) -> str:
    return f"{step_key}:{shipment_id}:{target}"


def _take(rt: Runtime, key: str, shipment_id: str) -> dict[str, Any] | None:
    with rt.sessionmaker() as session:
        return claims.take(session, key, shipment_id)


def _finish(rt: Runtime, key: str, result: dict[str, Any]) -> None:
    with rt.sessionmaker() as session:
        claims.finish(session, key, result)


def _fail(rt: Runtime, key: str, note: str) -> None:
    with rt.sessionmaker() as session:
        claims.fail(session, key, note)


def run_step(
    rt: Runtime, shipment_id: str, step_key: str, target: str, params: dict[str, Any], *, submits: bool
) -> Callable[[], Awaitable[dict[str, Any]]]:
    """Step handler: the SOP's run for ``target``. Returns what it read off the page."""
    step = portal_params.STEPS[step_key]

    async def run() -> dict[str, Any]:
        key = _key(step_key, shipment_id, target)
        if submits:
            try:
                earlier = await asyncio.to_thread(_take, rt, key, shipment_id)
            except claims.InFlight:
                raise inngest.NonRetriableError(
                    f"Another run is {step.title[0].lower()}{step.title[1:]} for {target}, or one stopped part way. "
                    f"Check the portal for {target} before trying again."
                ) from None
            if earlier is not None:
                return earlier
        try:
            outcome = await browser.run_sop(
                rt, str(step.sop), params, f"{step.title} for {target}",
                shipment_id=shipment_id, task_key=process.TASK_OF[step_key],
            )
        except Exception as exc:
            if submits:
                await asyncio.to_thread(_fail, rt, key, str(exc))
            raise
        reading = outcome.get("extraction_data") or {}
        if submits:
            await asyncio.to_thread(_finish, rt, key, reading)
        return reading

    return run


def check_portal_after(step_key: str) -> Callable[[Runtime, str, str], str]:
    """After a failed run of a step that submits: free the claims it held, and tell
    ops where to look, because the submit may have gone through before the failure."""

    def after(rt: Runtime, shipment_id: str, message: str) -> str:
        prefix = _key(step_key, shipment_id, "")
        with rt.sessionmaker() as session:
            claims.fail_open(session, shipment_id, prefix, message)
            unsure = [
                row.key.removeprefix(prefix)
                for row in session.query(IdempotencyKey).filter(
                    IdempotencyKey.shipment_id == shipment_id,
                    IdempotencyKey.key.startswith(prefix, autoescape=True),
                    IdempotencyKey.status == claims.FAILED,
                )
            ]
        if not unsure:
            return message
        return f"{message} Check the portal for {', '.join(unsure)} before trying again: it may have gone through."

    return after
