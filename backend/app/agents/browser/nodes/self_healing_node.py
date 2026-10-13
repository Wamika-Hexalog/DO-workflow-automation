"""
Self-Healing Node for LangGraph.

This node automatically detects and closes popups after navigation actions.
"""

import structlog

from ..core.state import AgentState
from ..utils.self_healing import SelfHealing
from ..integrations import LLMClient, StagehandClient

logger = structlog.get_logger(__name__)


class SelfHealingNode:
    """LangGraph node for automatic popup detection and removal."""

    def __init__(
        self,
        llm_client: LLMClient,
        stagehand_client: StagehandClient,
        max_attempts: int = 3,
        enabled_by_default: bool = True,
        repository=None,
    ):
        """
        Initialize self-healing node.

        Args:
            llm_client: LLM client for popup detection
            stagehand_client: Stagehand client to access browser page
            max_attempts: Maximum healing attempts per page
            enabled_by_default: Whether self-healing is enabled by default
            repository: Optional repository for saving self-healing events
        """
        self.llm_client = llm_client
        self.stagehand_client = stagehand_client
        self.max_attempts = max_attempts
        self.enabled_by_default = enabled_by_default
        self.repository = repository
        self.self_healing = SelfHealing(llm_client)

    async def execute(self, state: AgentState) -> AgentState:
        """
        Execute self-healing: detect and close popups.

        Flow:
        1. Check if self-healing is enabled
        2. Check if last action was navigation
        3. Wait for network idle
        4. Take screenshot
        5. Call LLM to detect popups
        6. Attempt to close if found
        7. Update state with results

        Args:
            state: Current agent state

        Returns:
            Updated agent state
        """
        # Check if self-healing is enabled
        self_healing_enabled = state.get("self_healing_enabled", self.enabled_by_default)

        if not self_healing_enabled:
            logger.debug("healing.skip", component="self_healing", reason="disabled")
            return state

        # Get last action result
        action_result = state.get("action_result", {})
        last_action_type = action_result.get("action_type", "")
        last_action_status = action_result.get("status", "")

        # Skip healing after "end" action (SOP complete)
        if last_action_type == "end":
            logger.debug("healing.skip", component="self_healing", reason="sop_ended")
            return state

        # Skip healing after HITL actions (wait_for_user)
        if last_action_type == "wait_for_user":
            logger.debug("healing.skip", component="self_healing", reason="hitl_completed")
            return state

        # Determine if healing is needed based on action type and status
        # Trigger healing in two scenarios:
        # 1. After successful navigation/click (preventive - catch popups that appear)
        # 2. After failed click (reactive - popup might be blocking the click)
        should_heal = False

        if last_action_type in ["goto", "click"] and last_action_status == "success":
            # Preventive: Check for popups after successful navigation/click
            should_heal = True
            logger.debug(
                "healing.preventive_check",
                component="self_healing",
                action_type=last_action_type,
            )
        elif last_action_type == "click" and last_action_status != "success":
            # Reactive: Failed click might be blocked by popup
            should_heal = True
            logger.info("healing.reactive_check", component="self_healing")

        if not should_heal:
            logger.debug(
                "healing.skip",
                component="self_healing",
                reason="no_healing_needed",
                action_type=last_action_type,
                action_status=last_action_status,
            )
            return state

        # Get the page. Prefer the active window context (the window the agent is
        # actually working in) so we don't screenshot the frozen first page — that
        # produced blank/white screenshots and useless popup analysis once new
        # windows opened. Fall back to current_page if the context is unavailable.
        try:
            active_context = state.get("active_context")
            if active_context:
                try:
                    page = self.stagehand_client.get_page_context(active_context)
                except Exception:
                    page = self.stagehand_client.current_page
            else:
                page = self.stagehand_client.current_page
            if not page:
                logger.warning("healing.no_page", component="self_healing")
                return state
        except Exception as e:
            logger.error(
                "healing.get_page_error", component="self_healing", error=str(e), handled=False
            )
            return state

        # Track healing attempts for this page
        healing_attempts = state.get("healing_attempts_current_page", 0)

        if healing_attempts >= self.max_attempts:
            logger.warning(
                "healing.max_attempts_reached",
                component="self_healing",
                max_attempts=self.max_attempts,
            )
            return state

        try:
            logger.info("healing.start", component="self_healing")

            # Wait for network to be idle (page fully loaded)
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
                logger.debug("healing.network_idle", component="self_healing")
            except Exception as e:
                logger.debug("healing.network_idle_timeout", component="self_healing", error=str(e))

            # Small additional wait for delayed popups
            await page.wait_for_timeout(1000)

            # PHASE 4: Use structured output for popup detection

            # Take screenshot for analysis
            screenshot_bytes = await page.screenshot(type="png")
            import base64

            screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")

            # Get structured popup analysis
            popup_prompt = self.self_healing._build_popup_detection_prompt()
            analysis_result = await self.llm_client.make_popup_analysis(
                screenshot_base64=screenshot_base64, prompt=popup_prompt
            )

            analysis = analysis_result["analysis"]
            detection_time_ms = analysis_result.get("duration_ms", 0)

            # Enhanced logging with confidence and risk
            logger.info(
                "healing.popup_analysis",
                component="self_healing",
                has_popup=analysis["has_popup"],
                popup_type=analysis["popup_type"],
                confidence=analysis["confidence"],
                risk_level=analysis["risk_level"],
                reasoning=analysis["reasoning"],
            )

            # Attempt to close if popup detected and blocking
            close_successful = False
            close_attempted = False

            if analysis["has_popup"] and analysis["blocking_content"]:
                # Check risk level before closing
                if analysis["risk_level"] == "high":
                    logger.warning(
                        "healing.high_risk_skip",
                        component="self_healing",
                        reasoning=analysis["reasoning"],
                    )
                else:
                    close_attempted = True
                    close_successful = await self.self_healing._close_popup(
                        page, analysis["close_method"], analysis.get("close_selector")
                    )

            # Save to database
            run_id = state.get("run_id")

            if self.repository and run_id:
                try:
                    await self.repository.save_self_healing_event(
                        run_id=run_id,
                        step_index=state.get("current_step_idx"),
                        has_popup=analysis["has_popup"],
                        popup_type=analysis["popup_type"],
                        confidence=analysis["confidence"],
                        risk_level=analysis["risk_level"],
                        reasoning=analysis["reasoning"],
                        blocking_content=analysis["blocking_content"],
                        close_attempted=close_attempted,
                        close_successful=close_successful,
                        attempts_count=1,
                        detection_time_ms=detection_time_ms,
                    )
                    logger.info("healing.db_saved", component="self_healing")
                except Exception as e:
                    logger.error(
                        "healing.db_save_failed",
                        component="self_healing",
                        error=str(e),
                        exc_info=True,
                        handled=False,
                    )
            else:
                logger.warning(
                    "healing.db_save_skipped",
                    component="self_healing",
                    has_repository=self.repository is not None,
                    # AutomationRun id, not the Inngest run — `state["run_id"]`
                    # is set from the browser agent's own current_run_id.
                    automation_run_id=run_id,
                )

            # Update state with results
            popups_closed = state.get("popups_closed", 0)

            if close_successful:
                popups_closed += 1
                logger.info(
                    "healing.popup_closed",
                    component="self_healing",
                    popup_type=analysis["popup_type"],
                )

                # Reset healing attempts after successful close
                state["healing_attempts_current_page"] = 0

                # Take another screenshot after closing for next step
                await page.wait_for_timeout(500)

            elif analysis["has_popup"] and close_attempted and not close_successful:
                logger.warning(
                    "healing.popup_close_failed",
                    component="self_healing",
                    popup_type=analysis["popup_type"],
                )
                state["healing_attempts_current_page"] = healing_attempts + 1
            else:
                logger.debug("healing.no_popup", component="self_healing")
                # Reset attempts counter if no popup
                state["healing_attempts_current_page"] = 0

            state["popups_closed"] = popups_closed
            state["last_healing_result"] = {
                "has_popup": analysis["has_popup"],
                "popup_type": analysis["popup_type"],
                "close_attempted": close_attempted,
                "close_successful": close_successful,
                "action_taken": f"{'Closed' if close_successful else 'Detected'} {analysis['popup_type']} popup"
                if analysis["has_popup"]
                else "No popup detected",
                "confidence": analysis["confidence"],
            }

        except Exception as e:
            logger.error("healing.error", component="self_healing", error=str(e), handled=False)
            state["last_healing_result"] = {
                "has_popup": False,
                "popup_type": "error",
                "close_attempted": False,
                "close_successful": False,
                "action_taken": f"Error: {str(e)}",
                "confidence": "low",
            }

        return state
