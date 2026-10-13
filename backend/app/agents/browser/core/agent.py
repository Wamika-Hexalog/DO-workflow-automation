import os
import asyncio
import time
from typing import Optional, Dict, Any
from uuid import UUID
from datetime import datetime
import structlog
from langsmith import traceable

from .state import AgentState
from .graph import create_agent_graph
from ..nodes import DecisionNode, ToolNode, SelfHealingNode
from ..integrations import StagehandClient, LLMClient
from ..utils import SOPReader, AgentLogger
from ..repository import BrowserRepository
from .._compat import settings
from .._compat import is_initialised as _db_is_initialised

logger = structlog.get_logger(__name__)


class LangGraphReactAgent:
    """A LangGraph-based React-style agent that takes SOP and decides next actions."""

    def __init__(
        self,
        headless: bool = False,
        max_iterations: int = 10,
        repository: Optional[BrowserRepository] = None,
    ):
        self.headless = headless
        self.max_iterations = max_iterations

        # Database integration. do-workflow: the caller passes a repository bound
        # to its SQLite database (python-brain built one on its global MongoDB).
        if repository is None:
            raise ValueError("Pass a BrowserRepository: the run log is kept in the app's database.")
        self.repository = repository
        self.current_run_id: Optional[UUID] = None

        # Validate API key
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError(
                "GOOGLE_API_KEY environment variable not set. "
                "Please set it with: export GOOGLE_API_KEY='your_api_key'"
            )

        # Initialize LLM client first
        self.llm_client = LLMClient(
            model="gemini-2.5-flash",
            temperature=0.1,
            api_key=api_key,
            enable_dynamic_switching=True,
        )

        # Initialize integration clients (pass LLM client to Stagehand)
        self.stagehand_client = StagehandClient(headless=headless, llm_client=self.llm_client)

        # Initialize node instances (will be set after browser starts)
        self.decision_node = None
        self.tool_node = None
        self.self_healing_node = None

        # Build the graph
        self.graph = create_agent_graph(
            self._decision_node, self._tool_node, self._self_healing_node, self._should_continue
        )

    @traceable(name="decision_node", run_type="chain")
    async def _decision_node(self, state: AgentState) -> AgentState:
        """Wrapper for the decision node."""
        if self.decision_node is None:
            raise RuntimeError("Decision node not initialized. Call start() first.")
        if not hasattr(self, "page") or self.page is None:
            raise RuntimeError(
                "Browser page not initialized. Call start() first or start() may have failed."
            )
        sop_reader = getattr(self, "sop_reader", None)
        result = await self.decision_node.execute(state, self.page, sop_reader)
        return result

    @traceable(name="tool_node", run_type="chain")
    async def _tool_node(self, state: AgentState) -> AgentState:
        """Wrapper for the tool node."""
        if self.tool_node is None:
            raise RuntimeError("Tool node not initialized. Call start() first.")
        result = await self.tool_node.execute(state)
        return result

    @traceable(name="self_healing_node", run_type="chain")
    async def _self_healing_node(self, state: AgentState) -> AgentState:
        """Wrapper for the self-healing node."""
        if self.self_healing_node is None:
            raise RuntimeError("Self-healing node not initialized. Call start() first.")
        result = await self.self_healing_node.execute(state)
        return result

    def _should_continue(self, state: AgentState) -> str:
        iteration_count = state.get("iteration_count", 0)
        stagehand_inputs = state.get("stagehand_inputs") or {}
        action_result = state.get("action_result") or {}
        messages = state.get("messages", [])

        if state.get("stop_execution"):
            logger.info("agent.stopping", component="agent", reason="stop_execution_flag")
            return "end"

        if stagehand_inputs.get("action_type") == "end":
            logger.info("agent.stopping", component="agent", reason="end_in_stagehand_inputs")
            return "end"

        if action_result.get("action_type") == "end":
            logger.info("agent.stopping", component="agent", reason="end_in_action_result")
            return "end"

        if messages:
            last_message = messages[-1]
            if "action" in last_message and last_message["action"].get("action_type") == "end":
                logger.info("agent.stopping", component="agent", reason="end_in_message")
                return "end"

        if iteration_count >= self.max_iterations:
            logger.info("agent.stopping", component="agent", reason="max_iterations")
            return "end"

        return "continue"

    @traceable(name="start_browser", run_type="tool")
    async def start(self):
        """Start the browser session and initialize integrations.

        In python-brain, MongoDB is initialised by the FastAPI lifespan before
        any Inngest function runs. This method therefore skips the standalone
        MongoDB init call that existed in browser-automation. If somehow called
        outside that context, it raises to surface the misconfiguration early.
        """
        if not _db_is_initialised():
            raise RuntimeError(
                "MongoDB is not initialised. "
                "Ensure init_mongodb() has been called (FastAPI lifespan) before starting the agent."
            )

        await self.stagehand_client.initialize()
        self.page = self.stagehand_client.current_page

        self.decision_node = DecisionNode(
            self.llm_client, self.max_iterations, repository=self.repository
        )
        self.tool_node = ToolNode(self.stagehand_client)
        self.self_healing_node = SelfHealingNode(
            llm_client=self.llm_client,
            stagehand_client=self.stagehand_client,
            max_attempts=3,
            enabled_by_default=True,
            repository=self.repository,
        )
        self.sop_reader = None

        logger.info("agent.session_start", component="agent", headless=self.headless)
        logger.info("agent.self_healing_enabled", component="agent")

    async def close(self):
        """Close the browser session and clean up state."""
        await self.stagehand_client.close()
        self.decision_node = None
        self.tool_node = None
        self.self_healing_node = None
        self.page = None
        self.sop_reader = None
        self.current_run_id = None
        logger.info("agent.session_closed", component="agent")

    def _sop_timeout_seconds(self) -> int:
        """How long one SOP may run before this agent times it out itself.

        The iteration budget alone loses a race it never declared. At
        max_iterations=160 it is 4800s, but a Browserbase session lives
        ``defaultTimeout`` seconds (3600 on prod-logistics-platform), so the reaper
        always killed the browser first: this guard could not fire, and the run
        ended by connection loss rather than by a named error.

        The clamp is therefore Browserbase-only. A local Chrome run has no reaper to
        lose to, and the duty lane deliberately budgets ~1-2h of peak-scaled browser
        work (workflows/customs/browser/logisys_queue.py — the checklist SOP alone
        allows a 12-min CSV import and a 10-min checklist wait, both scaled by
        SOP_TIMEOUT_SCALE). Clamping local runs would invent a 55-min ceiling that
        never existed and kill work that is merely slow.
        """
        budget = self.max_iterations * 30
        if getattr(self.stagehand_client, "use_browserbase", False):
            return min(budget, settings.browser_sop_max_seconds)
        return budget

    async def execute_sop(
        self,
        sop: str,
        initial_inputs: Optional[Dict[str, Any]] = None,
        sop_reader: Optional[SOPReader] = None,
        shipment_id: Optional[str] = None,
        task_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute a Standard Operating Procedure using the React-style agent.

        ``shipment_id`` (canonical Mongo id, optional) links the AutomationRun to
        its shipment and surfaces the Browserbase session on the Task Manager.
        ``task_key`` (e.g. "job_creation.logisys_job_creation") stores the session
        under automation.task_sessions.{stage}.{sub} so each task links to its own
        recording rather than the latest shipment-wide session.
        """
        if self.decision_node is None or self.tool_node is None:
            raise RuntimeError("Agent not initialized. Call start() before executing SOP.")

        if not sop or not sop.strip():
            raise ValueError("SOP cannot be empty")

        self.sop_reader = sop_reader

        if sop_reader:
            credentials_config = sop_reader.get_credentials_config()
            if credentials_config:
                self.tool_node.set_credentials_config(credentials_config)
            params_config = sop_reader.get_params()
            if params_config:
                self.tool_node.set_params_config(params_config)

        start_time = time.time()
        sop_metadata = sop_reader.get_metadata() if sop_reader else {}
        yaml_steps = sop_reader.get_steps() if sop_reader else []

        # Best-effort Browserbase session capture — never breaks the run.
        # session_id is set by start() (initialize()) which runs before execute_sop.
        bb_session_id = getattr(self.stagehand_client, "session_id", None)
        bb_url: str | None = None
        if bb_session_id:
            try:
                from ..integrations.browserbase_live import fetch_browserbase_live_url

                bb_url = await fetch_browserbase_live_url(bb_session_id)
            except Exception as e:
                logger.warning("browserbase.live_url_failed", component="agent", error=str(e))
            bb_url = bb_url or f"https://www.browserbase.com/sessions/{bb_session_id}"

        # do-workflow: shipment ids are plain strings, not Mongo ObjectIds.
        run_shipment_oid = shipment_id or None

        run = await self.repository.create_run(
            sop_name=sop_metadata.get("name", "Unknown SOP"),
            status="running",
            total_steps=len(yaml_steps) if yaml_steps else 0,
            shipment_id=run_shipment_oid,
            task_key=task_key,
            browserbase_session_id=bb_session_id,
            browserbase_live_url=bb_url,
        )

        self.current_run_id = run.run_id
        logger.info("db.run_created", component="database", automation_run_id=self.current_run_id)

        # Surface the session on the shipment so the Task Manager can link it.
        if run_shipment_oid and bb_url:
            try:
                from .._compat import get_shipment_repository

                await get_shipment_repository().set_browser_session_pointer(
                    shipment_id=run_shipment_oid,
                    session_id=bb_session_id,
                    url=bb_url,
                    status="running",
                    task_key=task_key,
                )
            except Exception as e:
                logger.warning("db.session_pointer_failed", component="agent", error=str(e))

        AgentLogger.sop_start(sop)

        if hasattr(self.llm_client, "reset_computer_use_history"):
            self.llm_client.reset_computer_use_history()

        initial_state = AgentState(
            messages=[],
            sop=sop,
            current_action=None,
            action_result=None,
            stagehand_inputs=initial_inputs or {},
            iteration_count=0,
            max_iterations=self.max_iterations,
            current_step_idx=0,
            consecutive_failures=0,
            run_id=self.current_run_id,
            # Self-healing is off by default for deterministic Playwright SOPs
            # (they handle their own popups and pay a Gemini vision call per step
            # otherwise). A SOP opts in with `self_healing: true` in its metadata.
            self_healing_enabled=sop_metadata.get("self_healing", False),
            popups_closed=0,
            healing_attempts_current_page=0,
            last_healing_result=None,
            active_context=None,
            stop_execution=False,
            sop_failed=False,
            sop_failure_reason=None,
        )

        # Tracks how to finalise the shipment session pointer (see finally below).
        session_final_status = "failed"

        try:
            timeout_seconds = self._sop_timeout_seconds()
            recursion_limit = self.max_iterations * 3 + 40

            try:
                final_state = await asyncio.wait_for(
                    self.graph.ainvoke(initial_state, config={"recursion_limit": recursion_limit}),
                    timeout=timeout_seconds,
                )
            except asyncio.TimeoutError:
                raise TimeoutError(
                    f"SOP execution exceeded {timeout_seconds}s timeout "
                    f"({self.max_iterations} iterations × 30s each)"
                )

            extracted_data: dict | None = None
            analysis_data = None
            messages = final_state.get("messages", [])

            for msg in reversed(messages):
                if "result" in msg:
                    result = msg.get("result", {})
                    action_type = result.get("action_type", "")
                    if action_type in ("extract", "execute_script") and result.get("extraction"):
                        extraction = result["extraction"]
                        if isinstance(extraction, dict):
                            # Accumulate all extract results; earlier (reversed=later) entries
                            # don't overwrite keys already set by newer extracts.
                            extracted_data = {**extraction, **(extracted_data or {})}
                        elif extracted_data is None:
                            extracted_data = extraction
                    if action_type == "act" and result.get("status") == "success":
                        if "analysis" in str(msg.get("content", "")).lower():
                            analysis_data = msg.get("content", "")

            iteration_count = final_state.get("iteration_count", 0)
            current_step_idx = final_state.get("current_step_idx", 0)

            if (
                self.decision_node
                and hasattr(self.decision_node, "complexity_analyzer")
                and hasattr(self.decision_node.complexity_analyzer, "yaml_steps")
                and self.decision_node.complexity_analyzer.yaml_steps
            ):
                total_steps = len(self.decision_node.complexity_analyzer.yaml_steps)
            else:
                total_steps = 5

            steps_completed = min(current_step_idx + 1, total_steps)

            # If decision_node signalled a graceful failure, return error WITH extraction_data
            # so callers (e.g. _step_run_browser_sop) can persist any job number that was
            # already extracted before the failing step.
            if final_state.get("sop_failed"):
                failure_reason = final_state.get("sop_failure_reason", "SOP failed")
                if self.current_run_id:
                    try:
                        duration = int(time.time() - start_time)
                        await self.repository.update_run(
                            self.current_run_id,
                            {
                                "status": "failed",
                                "completed_at": datetime.utcnow(),
                                "duration_seconds": duration,
                                "completed_steps": steps_completed,
                                "error_message": failure_reason,
                            },
                        )
                    except Exception as db_error:
                        logger.warning(
                            "db.update_failed",
                            component="database",
                            phase="graceful_failure",
                            error=str(db_error),
                        )
                AgentLogger.sop_complete("error", steps_completed, total_steps)
                # run_id + step progress let orchestrators make progress-aware
                # retry decisions (e.g. outbound_job's save-gate: a run that
                # died at/after the job-save click must never be blind-retried).
                return {
                    "status": "error",
                    "error": failure_reason,
                    "extraction_data": extracted_data,
                    "messages": messages,
                    "run_id": str(self.current_run_id) if self.current_run_id else None,
                    "steps_completed": steps_completed,
                    "total_steps": total_steps,
                }

            if extracted_data and self.current_run_id:
                try:
                    await self.repository.save_extraction(
                        run_id=self.current_run_id,
                        step_index=current_step_idx,
                        step_name="Extract data",
                        data=extracted_data
                        if isinstance(extracted_data, dict)
                        else {"extraction": str(extracted_data)},
                        is_empty=not bool(extracted_data),
                    )
                except Exception as e:
                    logger.warning("db.extraction_save_failed", component="database", error=str(e))

            if self.current_run_id:
                try:
                    duration = int(time.time() - start_time)
                    await self.repository.update_run(
                        self.current_run_id,
                        {
                            "status": "completed",
                            "completed_at": datetime.utcnow(),
                            "duration_seconds": duration,
                            "completed_steps": steps_completed,
                        },
                    )
                except Exception as e:
                    logger.warning("db.update_failed", component="database", error=str(e))

            AgentLogger.sop_complete(
                "completed", steps_completed, total_steps, extracted_data or analysis_data
            )

            session_final_status = "completed"
            return {
                "status": "completed",
                "final_state": final_state,
                "messages": messages,
                "iterations": iteration_count,
                "extraction_data": extracted_data,
                "analysis_data": analysis_data,
                "run_id": str(self.current_run_id) if self.current_run_id else None,
                "steps_completed": steps_completed,
                "total_steps": total_steps,
            }

        except Exception as e:
            AgentLogger.error(f"Error during SOP execution: {str(e)}", handled=False)

            if (
                self.decision_node
                and hasattr(self.decision_node, "complexity_analyzer")
                and hasattr(self.decision_node.complexity_analyzer, "yaml_steps")
                and self.decision_node.complexity_analyzer.yaml_steps
            ):
                total_steps = len(self.decision_node.complexity_analyzer.yaml_steps)
            else:
                total_steps = 5

            if "initial_state" in locals():
                current_step_idx = initial_state.get("current_step_idx", 0)
                messages = initial_state.get("messages", [])
            else:
                current_step_idx = 0
                messages = []

            steps_completed = min(current_step_idx + 1, total_steps)

            if self.current_run_id:
                try:
                    duration = int(time.time() - start_time)
                    await self.repository.update_run(
                        self.current_run_id,
                        {
                            "status": "failed",
                            "completed_at": datetime.utcnow(),
                            "duration_seconds": duration,
                            "completed_steps": steps_completed,
                            "error_message": str(e),
                        },
                    )
                except Exception as db_error:
                    logger.warning(
                        "db.update_failed",
                        component="database",
                        phase="on_error",
                        error=str(db_error),
                    )

            AgentLogger.sop_complete("error", steps_completed, total_steps)

            # steps_completed is None here on purpose: this path reads the
            # IMMUTABLE initial_state, so the count is fabricated (always ~1)
            # rather than observed. Reporting a small integer would make the
            # outbound save-gate classify a post-Save graph crash as safely
            # pre-Save and re-create the LogiSys job (Codex review P1). None →
            # progress unknowable → callers take the conservative park path.
            return {
                "status": "error",
                "error": str(e),
                "messages": messages,
                "run_id": str(self.current_run_id) if self.current_run_id else None,
                "steps_completed": None,
                "total_steps": total_steps,
            }

        finally:
            # Flip the shipment session pointer off "running" once the run ends,
            # swapping the (now-dead) live URL for the login-gated replay URL, so
            # the Task Manager stops labelling a finished session as "watch live".
            # Best-effort — never affects the run outcome.
            if run_shipment_oid and bb_session_id:
                try:
                    from .._compat import get_shipment_repository

                    await get_shipment_repository().set_browser_session_pointer(
                        shipment_id=run_shipment_oid,
                        session_id=bb_session_id,
                        url=f"https://www.browserbase.com/sessions/{bb_session_id}",
                        status=session_final_status,
                        task_key=task_key,
                    )
                except Exception as e:
                    logger.warning(
                        "db.session_pointer_finalize_failed", component="agent", error=str(e)
                    )
