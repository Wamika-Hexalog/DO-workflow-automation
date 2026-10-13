"""Helpers shared by the workflow functions."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import inngest

from .. import letters, pdfs, service
from ..runtime import Runtime

# Errors a retry can't fix: the run fails at once and ops see the message.
FINAL_ERRORS = (service.NotReady, service.NotFound, letters.MissingAsset, pdfs.UnreadableFile)


def job(fn: Callable[..., Any], *args: Any) -> Callable[[], Awaitable[Any]]:
    """A step handler that runs a blocking job in a worker thread, so a build or
    a database write never stalls the API sharing this process."""

    async def run() -> Any:
        try:
            return await asyncio.to_thread(fn, *args)
        except FINAL_ERRORS as exc:
            raise inngest.NonRetriableError(str(exc)) from exc

    return run


def failure_details(ctx: inngest.Context) -> tuple[str | None, str]:
    """The shipment and error from an ``inngest/function.failed`` event."""
    data = ctx.event.data or {}
    original = data.get("event") or {}
    shipment_id = (original.get("data") or {}).get("shipment_id")
    error = data.get("error") or {}
    message = error.get("message") if isinstance(error, dict) else str(error)
    return shipment_id, message or "The workflow failed without a message."


def fail_task_handler(
    rt: Runtime, task_key: str, after: Callable[[Runtime, str, str], str] | None = None
) -> Callable[[inngest.Context], Awaitable[None]]:
    """on_failure handler: put the error on the task, so ops see it and can retry.
    ``after`` may clean up and return a longer message."""

    async def handler(ctx: inngest.Context) -> None:
        shipment_id, message = failure_details(ctx)
        if not shipment_id:
            return
        if after is not None:
            message = await asyncio.to_thread(after, rt, shipment_id, message)
        await asyncio.to_thread(service.fail_task_job, rt, shipment_id, task_key, message)

    return handler


def shipment_id_of(ctx: inngest.Context) -> str:
    shipment_id = (ctx.event.data or {}).get("shipment_id")
    if not shipment_id:
        raise inngest.NonRetriableError(f"{ctx.event.name} has no shipment_id.")
    return str(shipment_id)

