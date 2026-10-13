"""Where python-brain's browser agent reached into the rest of python-brain.

Everything under app/agents/browser is python-brain's src/agents/browser at
commit 232f3b0, changed only in three places so it can be re-synced:

* the copied files import the names below instead of ``shared.*``;
* repository.py keeps the run log in this app's SQLite database, not MongoDB;
* core/agent.py takes that repository as an argument, uses this app's string
  shipment ids instead of Mongo ObjectIds, and records the task key on the run.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any

import structlog

from .utils.log_redaction import redact_text

logger = structlog.get_logger(__name__)


def _number(name: str, default: float, low: float, high: float) -> float:
    """python-brain clamps these rather than refusing to start on bad input."""
    try:
        value = float(os.getenv(name, default))
    except ValueError:
        return default
    if not math.isfinite(value):
        return default
    return min(max(value, low), high)


@dataclass(frozen=True)
class EngineSettings:
    """The python-brain settings the agent reads, with its variable names and defaults."""

    # Scales navigation timeouts for slow portal hours.
    sop_timeout_scale: float = 2.0
    # Bound on each local browser teardown await.
    browser_teardown_timeout_s: float = 10.0
    # Ceiling on one SOP's browser time on Browserbase.
    browser_sop_max_seconds: int = 3300

    @classmethod
    def from_env(cls) -> EngineSettings:
        return cls(
            sop_timeout_scale=_number("SOP_TIMEOUT_SCALE", 2.0, 1.0, 10.0),
            browser_teardown_timeout_s=_number("BROWSER_TEARDOWN_TIMEOUT_S", 10.0, 0.1, 120.0),
            browser_sop_max_seconds=int(_number("BROWSER_SOP_MAX_SECONDS", 3300, 1, 3540)),
        )


settings = EngineSettings.from_env()


def is_initialised() -> bool:
    """python-brain checked MongoDB here. The run log is SQLite, handed to the
    agent through BrowserRepository, so there is nothing to set up first."""
    return True


class _NoTaskManager:
    async def set_browser_session_pointer(self, **_: Any) -> None:
        """python-brain linked a Browserbase session on its Task Manager. Here the
        run log keeps the session URL instead."""


def get_shipment_repository() -> _NoTaskManager:
    return _NoTaskManager()


async def send_tech_alert(payload: dict[str, Any]) -> None:
    """python-brain sent these to Outlook and Slack; logged here until alerts exist."""
    logger.warning("browser.tech_alert", **{f"alert_{key}": str(value) for key, value in payload.items()})


__all__ = ["get_shipment_repository", "is_initialised", "redact_text", "send_tech_alert", "settings"]
