"""The DO folder: check the documents as they arrive, build the PDF once ops confirm.

    customs/do.document.received ─┐
    customs/do.shipment.updated ──┴→ do-folder-verify → customs/do.folder.ready
    customs/task.completed[folder_review] → do-folder-build → customs/do.docs.ready
"""
from __future__ import annotations

from datetime import timedelta

import inngest

from .. import service, tasks
from ..runtime import Runtime
from .events import EventNames
from .steps import fail_task_handler, job, shipment_id_of


def create_folder_verify_function(rt: Runtime) -> inngest.Function:
    @rt.inngest.create_function(
        fn_id="do-folder-verify",
        name="DO folder: check documents",
        trigger=[
            inngest.TriggerEvent(event=EventNames.DOCUMENT_RECEIVED),
            inngest.TriggerEvent(event=EventNames.SHIPMENT_UPDATED),
        ],
        # Autosave sends a burst of updates while someone types; check once it settles.
        debounce=inngest.Debounce(period=timedelta(seconds=2), key="event.data.shipment_id"),
        retries=2,
    )
    async def verify(ctx: inngest.Context) -> dict:
        shipment_id = shipment_id_of(ctx)
        result = await ctx.step.run("verify", job(service.refresh_folder_review_job, rt, shipment_id))
        if result["became_ready"]:
            await ctx.step.send_event(
                "folder-ready", inngest.Event(name=EventNames.FOLDER_READY, data={"shipment_id": shipment_id})
            )
        return result

    return verify


def create_folder_build_function(rt: Runtime) -> inngest.Function:
    @rt.inngest.create_function(
        fn_id="do-folder-build",
        name="DO folder: build the PDF",
        trigger=inngest.TriggerEvent(
            event=EventNames.TASK_COMPLETED, expression=f'event.data.task_key == "{tasks.FOLDER_REVIEW}"'
        ),
        concurrency=[inngest.Concurrency(limit=1, key="event.data.shipment_id")],
        retries=1,
        on_failure=fail_task_handler(rt, tasks.FOLDER_REVIEW),
    )
    async def build(ctx: inngest.Context) -> dict:
        shipment_id = shipment_id_of(ctx)
        built = await ctx.step.run("build-pdf", job(service.build_folder_job, rt, shipment_id))
        do_request = await ctx.step.run("open-do-request", job(service.open_do_request_job, rt, shipment_id))
        await ctx.step.send_event(
            "docs-ready",
            inngest.Event(
                name=EventNames.DOCS_READY, data={"shipment_id": shipment_id, "filename": built["filename"]}
            ),
        )
        return {"built": built, "do_request": do_request}

    return build
