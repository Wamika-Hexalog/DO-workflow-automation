"""BrowserRepository — the browser agent's run log, in this app's SQLite database.

do-workflow: python-brain's version wraps its Beanie ``AutomationRun`` in MongoDB.
This one keeps the same methods and signatures over the ``automation_runs``
table (app/db.py), so the agent code calling it is unchanged. Writes happen in
a worker thread, so a run never blocks the API sharing the event loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from ...db import AutomationRun

logger = logging.getLogger(__name__)

# Fields create_run accepts besides sop_name, status and total_steps.
_RUN_FIELDS = {"shipment_id", "task_key", "browserbase_session_id", "browserbase_live_url"}


def _plain(value: Any) -> Any:
    """JSON-safe copy: extractions can hold whatever a page script returned."""
    return json.loads(json.dumps(value, default=str))


def _now() -> datetime:
    return datetime.now(timezone.utc)


class BrowserRepository:
    """Repository for browser-workflow DB operations."""

    def __init__(self, sessions: sessionmaker) -> None:
        self._sessions = sessions

    async def _write(self, run_id: UUID, change) -> bool:  # noqa: ANN001 - change(row) mutates the run
        def write() -> bool:
            with self._sessions() as session:
                row = session.get(AutomationRun, str(run_id))
                if row is None:
                    logger.warning("Run %s not found", run_id)
                    return False
                change(row)
                session.commit()
                return True

        return await asyncio.to_thread(write)

    # ── Automation Runs ───────────────────────────────────────────────────────

    async def create_run(self, sop_name: str, status: str, total_steps: int, **kwargs: Any) -> SimpleNamespace:
        """Returns an object with ``run_id`` (a UUID), which is all the agent reads."""
        run_id = uuid4()
        fields = {key: value for key, value in kwargs.items() if key in _RUN_FIELDS and value is not None}

        def write() -> None:
            with self._sessions() as session:
                session.add(
                    AutomationRun(
                        run_id=str(run_id),
                        sop_name=sop_name,
                        status=status,
                        total_steps=total_steps,
                        started_at=_now(),
                        extractions=[],
                        llm_decisions=[],
                        stagehand_calls=[],
                        self_healing_events=[],
                        **fields,
                    )
                )
                session.commit()

        await asyncio.to_thread(write)
        logger.info("Created automation run: %s", run_id)
        return SimpleNamespace(run_id=run_id, sop_name=sop_name, status=status)

    async def update_run(self, run_id: UUID, updates: dict[str, Any]) -> bool:
        def change(row: AutomationRun) -> None:
            for key, value in updates.items():
                if hasattr(row, key):
                    setattr(row, key, value)

        updated = await self._write(run_id, change)
        if updated:
            logger.info("Updated automation run: %s", run_id)
        return updated

    async def get_run(self, run_id: UUID) -> AutomationRun | None:
        def read() -> AutomationRun | None:
            with self._sessions() as session:
                return session.get(AutomationRun, str(run_id))

        return await asyncio.to_thread(read)

    async def get_recent_runs(self, limit: int = 10) -> list[AutomationRun]:
        def read() -> list[AutomationRun]:
            with self._sessions() as session:
                return list(
                    session.scalars(select(AutomationRun).order_by(AutomationRun.started_at.desc()).limit(limit))
                )

        return await asyncio.to_thread(read)

    # ── Embedded trace helpers ────────────────────────────────────────────────
    # Each appends to a JSON list on the run, the way python-brain embeds them.

    async def _append(self, run_id: UUID, column: str, record: dict[str, Any]) -> bool:
        def change(row: AutomationRun) -> None:
            # A new list, so SQLAlchemy sees the JSON column change.
            setattr(row, column, [*(getattr(row, column) or []), _plain({**record, "created_at": _now()})])

        return await self._write(run_id, change)

    async def save_extraction(
        self,
        run_id: UUID,
        step_index: int,
        step_name: str | None,
        data: dict[str, Any],
        is_empty: bool = False,
    ) -> bool:
        return await self._append(
            run_id,
            "extractions",
            {"step_index": step_index, "step_name": step_name, "data": data, "is_empty": is_empty},
        )

    async def save_llm_decision(
        self,
        run_id: UUID,
        step_index: int,
        decision_type: str,
        decision_data: dict[str, Any],
        model_used: str,
        tokens_input: int | None = None,
        tokens_output: int | None = None,
        cost_usd: float | None = None,
        duration_ms: int | None = None,
        prompt_summary: str | None = None,
    ) -> bool:
        def change(row: AutomationRun) -> None:
            record = {
                "step_index": step_index,
                "decision_type": decision_type,
                "reasoning": decision_data.get("reasoning", ""),
                "chosen_action": decision_data.get("chosen_action"),
                "confidence": decision_data.get("confidence"),
                "risk_level": decision_data.get("risk_level"),
                "alternatives": decision_data.get("alternatives", []),
                "model_used": model_used,
                "tokens_input": tokens_input,
                "tokens_output": tokens_output,
                "cost_usd": cost_usd,
                "duration_ms": duration_ms,
                "prompt_summary": prompt_summary,
                "created_at": _now(),
            }
            row.llm_decisions = [*(row.llm_decisions or []), _plain(record)]
            row.total_llm_calls = (row.total_llm_calls or 0) + 1
            row.total_tokens = (row.total_tokens or 0) + (tokens_input or 0) + (tokens_output or 0)
            row.total_cost_usd = (row.total_cost_usd or 0.0) + (cost_usd or 0.0)

        return await self._write(run_id, change)

    async def save_stagehand_call(
        self,
        run_id: UUID,
        step_index: int,
        call_type: str,
        instruction: str,
        status: str,
        duration_ms: int,
        result_data: dict[str, Any] | None = None,
        error_message: str | None = None,
        estimated_tokens: int | None = None,
        estimated_cost_usd: float | None = None,
        window_context: str | None = None,
    ) -> bool:
        return await self._append(
            run_id,
            "stagehand_calls",
            {
                "step_index": step_index,
                "call_type": call_type,
                "instruction": instruction,
                "status": status,
                "result_data": result_data,
                "error_message": error_message,
                "duration_ms": duration_ms,
                "estimated_tokens": estimated_tokens,
                "estimated_cost_usd": estimated_cost_usd,
                "window_context": window_context,
            },
        )

    async def save_self_healing_event(
        self,
        run_id: UUID,
        step_index: int | None,
        has_popup: bool,
        popup_type: str | None,
        confidence: float | None,
        risk_level: str | None,
        reasoning: str | None,
        blocking_content: bool,
        close_attempted: bool,
        close_successful: bool | None,
        attempts_count: int,
        detection_time_ms: int | None,
    ) -> bool:
        return await self._append(
            run_id,
            "self_healing_events",
            {
                "step_index": step_index,
                "has_popup": has_popup,
                "popup_type": popup_type,
                "confidence": confidence,
                "risk_level": risk_level,
                "reasoning": reasoning,
                "blocking_content": blocking_content,
                "close_attempted": close_attempted,
                "close_successful": close_successful,
                "attempts_count": attempts_count,
                "detection_time_ms": detection_time_ms,
            },
        )
