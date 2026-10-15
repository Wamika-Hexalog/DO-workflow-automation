"""The eDO: once CMA CGM's mail says it's issued, collect it from ODeX for each house
bill. The job is done when every house bill has its eDO.

    customs/do.edo.received → do-edo-collect → customs/do.case.completed
"""
from __future__ import annotations

import inngest

from .. import portal_params, process, tasks
from ..runtime import Runtime
from .events import EventNames
from .portal import ODEX_BROWSER, run_step
from .steps import fail_task_handler, job, shipment_id_of


def create_edo_collect_function(rt: Runtime) -> inngest.Function:
    step_key = portal_params.DO_REQUEST_STATUS

    @rt.inngest.create_function(
        fn_id="do-edo-collect",
        name="eDO: collect it from ODeX",
        trigger=[
            inngest.TriggerEvent(event=EventNames.EDO_RECEIVED),
            inngest.TriggerEvent(event=EventNames.TASK_COMPLETED, expression=f'event.data.task_key == "{tasks.ODEX_EDO}"'),
        ],
        concurrency=ODEX_BROWSER,
        retries=1,  # it only reads
        on_failure=fail_task_handler(rt, tasks.ODEX_EDO),
    )
    async def collect(ctx: inngest.Context) -> dict:
        shipment_id = shipment_id_of(ctx)
        retry = ctx.event.name == EventNames.TASK_COMPLETED
        start = await ctx.step.run("start", job(process.start_job, rt, shipment_id, tasks.ODEX_EDO, retry))
        if not start["run"]:
            return start
        params = await ctx.step.run("params", job(portal_params.params_job, rt, shipment_id, step_key))
        readings = {}
        for hbl, values in params.items():
            readings[hbl] = await ctx.step.run(
                f"collect-from-odex:{hbl}", run_step(rt, shipment_id, step_key, hbl, values, submits=False)
            )
        result = await ctx.step.run("record", job(process.record_job, rt, shipment_id, step_key, readings))
        if result["finished"]:
            await ctx.step.send_event(
                "case-completed", inngest.Event(name=EventNames.CASE_COMPLETED, data={"shipment_id": shipment_id})
            )
        return result

    return collect
