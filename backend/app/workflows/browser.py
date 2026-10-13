"""Runs one browser SOP with the agent copied from python-brain.

Adapted from ``_run_sop`` in python-brain's workflows/customs/browser/duty_structure.py
(commit 232f3b0): the run is logged to this app's SQLite database instead of
MongoDB, the browser's visibility is a setting, and the run id comes back so the
caller can link to the run log.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import inngest

from ..runtime import Runtime


async def run_sop(
    rt: Runtime,
    sop_path: str,
    params: dict[str, Any],
    job_label: str,
    *,
    shipment_id: str = "",
    task_key: str = "",
    max_iterations: int = 80,
) -> dict[str, Any]:
    """Start the agent, run the SOP, close the agent. Raises NonRetriableError on failure.

    max_iterations also bounds wall-clock (30s per iteration in execute_sop):
    raise it for SOPs with long in-page waits.
    """
    # Imported here, as in python-brain, so the API starts without Playwright loaded.
    from ..agents.browser.core.agent import LangGraphReactAgent
    from ..agents.browser.repository import BrowserRepository
    from ..agents.browser.utils.sop_reader import SOPReader

    if not Path(sop_path).is_file():
        raise inngest.NonRetriableError(f"No SOP at {sop_path} for {job_label}.")

    agent = None
    try:
        try:
            # Constructor included: it validates env config, and a raise here is a
            # browser-init problem, not something a step retry would fix.
            agent = LangGraphReactAgent(
                headless=rt.settings.browser_headless,
                max_iterations=max_iterations,
                repository=BrowserRepository(rt.sessionmaker),
            )
            await agent.start()
        except Exception as e:
            raise inngest.NonRetriableError(f"Browser init failed for {job_label}: {e}") from None

        sop_reader = SOPReader(sop_path, params=params)
        sop = sop_reader.read_sop()
        if not sop:
            raise inngest.NonRetriableError(f"Failed to read SOP: {sop_path}")

        yaml_steps = sop_reader.get_steps()
        if yaml_steps and agent.decision_node:
            agent.decision_node.complexity_analyzer.set_yaml_steps(yaml_steps)

        try:
            result = await agent.execute_sop(
                sop,
                sop_reader=sop_reader,
                shipment_id=shipment_id or None,
                task_key=task_key or None,
            )
        except RuntimeError as e:
            raise inngest.NonRetriableError(str(e)) from None
        except Exception as e:
            raise inngest.NonRetriableError(f"SOP raised exception for {job_label}: {e}") from None
    finally:
        try:
            if agent is not None:
                await agent.close()
        except Exception:
            pass

    if result.get("status") != "completed":
        raise inngest.NonRetriableError(f"SOP did not complete for {job_label}: {result.get('error')}")

    # Only JSON-serializable fields: final_state and messages hold LangGraph objects.
    return {
        "status": "completed",
        "job": job_label,
        "iterations": result.get("iterations"),
        "extraction_data": result.get("extraction_data") or {},
        "run_id": result.get("run_id"),
    }
