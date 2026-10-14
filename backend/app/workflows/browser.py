"""Runs one browser SOP with the agent copied from python-brain.

Adapted from ``_run_sop`` in python-brain's workflows/customs/browser/duty_structure.py
(commit 232f3b0): the run is logged to this app's SQLite database instead of
MongoDB, the browser's visibility is a setting, and the run id comes back so the
caller can link to the run log.
"""
from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import inngest

from ..runtime import Runtime

logger = logging.getLogger(__name__)

# Pages the browser makes for itself, or reads from this machine's disk.
_LOCAL_SCHEMES = {"file", "data", "blob", "about", "chrome", "chrome-error"}


def host_allowed(url: str, allowed: tuple[str, ...]) -> bool:
    parts = urlsplit(url)
    if parts.scheme in _LOCAL_SCHEMES:
        return True
    host = (parts.hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in allowed)


async def guard_hosts(context: Any, allowed: tuple[str, ...], blocked: list[str]) -> None:
    """Blocks every request to a host that isn't allowed, so the agent can't reach
    a real site unless the settings say it may (DO_BROWSER_ALLOWED_HOSTS)."""

    async def route(request_route: Any) -> None:
        url = request_route.request.url
        if host_allowed(url, allowed):
            await request_route.continue_()
            return
        if request_route.request.is_navigation_request():
            blocked.append(urlsplit(url).hostname or url)
        logger.warning("Blocked a request to %s: not in DO_BROWSER_ALLOWED_HOSTS", urlsplit(url).hostname)
        await request_route.abort("blockedbyclient")

    await context.route("**/*", route)


@contextmanager
def _model_key_for(steps: list[dict[str, Any]]) -> Iterator[None]:
    """python-brain's agent won't start without GOOGLE_API_KEY, even for an SOP whose
    steps are all scripted Playwright steps that never ask the model anything. For
    such an SOP a placeholder stands in while the agent starts, then goes again."""
    scripted = all(step.get("method") == "playwright" or step.get("action") == "end" for step in steps)
    if os.getenv("GOOGLE_API_KEY") or not scripted:
        yield
        return
    os.environ["GOOGLE_API_KEY"] = "not-used-by-playwright-steps"
    try:
        yield
    finally:
        os.environ.pop("GOOGLE_API_KEY", None)


def step_error(result: dict[str, Any]) -> str | None:
    """What the failing step said, such as a portal's own rejection message. The
    agent's summary ("Step failed 3 consecutive times: ...") leaves it out."""
    for message in reversed(result.get("messages") or []):
        step = message.get("result") if isinstance(message, dict) else None
        if isinstance(step, dict) and step.get("status") == "error" and step.get("message"):
            # Playwright puts its call and the JS error's type first: "Page.evaluate: Error: ...".
            first_line = str(step["message"]).strip().splitlines()[0]
            return re.sub(r"^(?:\w+\.\w+: )?(?:Error: )?", "", first_line).strip()
    return None


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
    # The agent only stops early at an `action: end` step; without one it repeats
    # the last step until its iterations run out. On a step that submits, such as
    # ODeX's DO request, that would mean submitting again and again.
    check = SOPReader(sop_path)
    steps = check.get_steps() if check.read_sop() else []
    if not steps or steps[-1].get("action") != "end":
        raise inngest.NonRetriableError(
            f"{Path(sop_path).name} must finish with an `action: end` step, or the agent repeats its last step."
        )

    agent = None
    blocked: list[str] = []
    allowed = rt.settings.browser_allowed_hosts
    try:
        try:
            # Constructor included: it validates env config, and a raise here is a
            # browser-init problem, not something a step retry would fix.
            with _model_key_for(steps):
                agent = LangGraphReactAgent(
                    headless=rt.settings.browser_headless,
                    max_iterations=max_iterations,
                    repository=BrowserRepository(rt.sessionmaker),
                )
                await agent.start()
            if allowed is not None:
                await guard_hosts(agent.stagehand_client.context, allowed, blocked)
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
        if blocked:
            raise inngest.NonRetriableError(
                f"SOP did not complete for {job_label}: it tried to open {', '.join(sorted(set(blocked)))}, "
                f"which isn't allowed. Allowed hosts: {', '.join(allowed or ())} (DO_BROWSER_ALLOWED_HOSTS)."
            )
        failure = result.get("error") or "the agent stopped without saying why"
        if said := step_error(result):
            raise inngest.NonRetriableError(f"SOP did not complete for {job_label}: {said} ({failure})")
        raise inngest.NonRetriableError(f"SOP did not complete for {job_label}: {failure}")

    # Only JSON-serializable fields: final_state and messages hold LangGraph objects.
    return {
        "status": "completed",
        "job": job_label,
        "iterations": result.get("iterations"),
        "extraction_data": result.get("extraction_data") or {},
        "run_id": result.get("run_id"),
    }
