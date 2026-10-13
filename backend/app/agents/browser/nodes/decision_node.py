"""
Decision node implementation for the Browser Auto agent.

This module contains the decision node logic that analyzes the SOP and current state
to decide the next action using LLM reasoning.
"""

from typing import Dict, Any, Optional

import structlog

from ..core.state import AgentState
from ..integrations import LLMClient
from ..utils import ScreenshotUtils, TaskComplexityAnalyzer, AgentLogger

logger = structlog.get_logger(__name__)


class DecisionNode:
    """Decision node that analyzes SOP and current state to decide next action."""

    def __init__(self, llm_client: LLMClient, max_iterations: int, repository=None):
        """
        Initialize the decision node.

        Args:
            llm_client: The LLM client for decision making
            max_iterations: Maximum number of iterations
            repository: Optional repository for saving structured LLM decisions
        """
        self.llm_client = llm_client
        self.max_iterations = max_iterations
        self.complexity_analyzer = TaskComplexityAnalyzer()
        self.repository = repository

    async def execute(self, state: AgentState, page=None, sop_reader=None) -> AgentState:
        """
        Execute the decision node logic.

        Args:
            state: Current agent state
            page: Optional page object for screenshots
            sop_reader: Optional SOPReader for YAML-driven execution

        Returns:
            Updated state with next action
        """
        logger.info("decision.analyzing", component="decision")

        # Check if we're in YAML-driven mode with Playwright steps
        if sop_reader:
            current_step_idx = state.get("current_step_idx", 0)
            iteration_count = state.get("iteration_count", 0)
            action_result = state.get("action_result")
            consecutive_failures = state.get("consecutive_failures", 0)

            # STEP ADVANCEMENT: Handle this FIRST, before fetching step config
            if iteration_count > 0 and action_result:
                yaml_steps = self.complexity_analyzer.yaml_steps

                # Validate yaml_steps is a list
                if yaml_steps is not None and not isinstance(yaml_steps, list):
                    yaml_steps = None

                # Check if we need to retry the current step (e.g., after emergency HITL)
                if state.get("retry_current_step"):
                    logger.info("decision.retry_current_step", component="decision")
                    state["retry_current_step"] = False  # Clear the flag
                    consecutive_failures = 0  # Reset failures
                    state["consecutive_failures"] = consecutive_failures
                    # Don't advance step, just continue with current step
                elif action_result.get("status") == "success":
                    # Action succeeded - advance to next step
                    if (
                        yaml_steps
                        and len(yaml_steps) > 0
                        and current_step_idx < len(yaml_steps) - 1
                    ):
                        current_step_idx = current_step_idx + 1
                        logger.info(
                            "decision.advance_step",
                            component="decision",
                            step_number=current_step_idx + 1,
                        )
                    consecutive_failures = 0  # Reset failure counter
                else:
                    # Action failed - increment failure counter
                    consecutive_failures += 1
                    logger.warning(
                        "decision.step_failed",
                        component="decision",
                        consecutive_failures=consecutive_failures,
                    )

                    # If too many failures, check if blocked by popup or genuine failure
                    if consecutive_failures >= 3:
                        # Check if self-healing detected a blocking popup it couldn't close
                        # (the key is initialised to None, so the default never applies)
                        last_healing = state.get("last_healing_result") or {}
                        healing_attempts = state.get("healing_attempts_current_page", 0)

                        if (
                            last_healing.get("has_popup")
                            and not last_healing.get("close_successful")
                            and healing_attempts >= 3
                        ):
                            # EMERGENCY HITL: Blocking popup detected but couldn't close
                            logger.error("decision.blocking_popup", component="decision")

                            # Get current step description
                            if yaml_steps and 0 <= current_step_idx < len(yaml_steps):
                                current_step_desc = yaml_steps[current_step_idx].get(
                                    "description", "Unknown step"
                                )
                            else:
                                current_step_desc = "Unknown step"

                            # Inject emergency HITL step
                            state["requires_emergency_hitl"] = True
                            state["emergency_hitl_message"] = (
                                f"\n🚨 AUTOMATION PAUSED - MANUAL INTERVENTION REQUIRED 🚨\n\n"
                                f"Step that failed: {current_step_desc}\n"
                                f"Issue: {last_healing.get('popup_type', 'popup').upper()} popup is blocking automation\n"
                                f"Self-healing attempts: {healing_attempts}/3 (all failed)\n\n"
                                f"⚠️  Action needed:\n"
                                f"  1. Look at the browser window\n"
                                f"  2. Manually close any popups, alerts, or blocking overlays\n"
                                f"  3. Press Enter to retry the failed step\n\n"
                                f"Press Enter when ready..."
                            )

                            # Reset counters so we retry after HITL
                            consecutive_failures = 0
                            state["healing_attempts_current_page"] = 0

                            # Update state and RETURN immediately so tool node can handle HITL
                            state["current_step_idx"] = current_step_idx
                            state["consecutive_failures"] = consecutive_failures

                            logger.info("decision.emergency_hitl_injected", component="decision")

                            # Return immediately - don't continue processing
                            # The tool node will handle the HITL pause and then retry
                            return state
                        else:
                            # Genuine failure (not popup-related) — set flags and route to END
                            # so ainvoke() returns normally and any extraction data is preserved.
                            logger.error("decision.step_failed_stopping", component="decision")
                            if yaml_steps and 0 <= current_step_idx < len(yaml_steps):
                                current_step_desc = yaml_steps[current_step_idx].get(
                                    "description", "Unknown step"
                                )
                            else:
                                current_step_desc = "Unknown step"

                            failure_reason = (
                                f"Step failed 3 consecutive times: {current_step_desc}. "
                                f"The target never became ready within its timeout and "
                                f"self-healing found no recognised popup to close. Treating "
                                f"as a terminal automation failure."
                            )
                            AgentLogger.error(failure_reason)
                            state["sop_failed"] = True
                            state["sop_failure_reason"] = failure_reason
                            state["stop_execution"] = True
                            state["current_step_idx"] = current_step_idx
                            state["consecutive_failures"] = consecutive_failures
                            return state

                # Update state with new step index and failure count
                state["current_step_idx"] = current_step_idx
                state["consecutive_failures"] = consecutive_failures

            # NOW fetch the step config for the (potentially advanced) step
            try:
                step_config = sop_reader.get_step_config(current_step_idx)
            except Exception as e:
                logger.error(
                    "decision.step_config_failed",
                    component="decision",
                    step_index=current_step_idx,
                    error=str(e),
                    handled=False,
                )
                step_config = None

            if step_config and step_config.get("method") == "playwright":
                # Playwright step - skip LLM, pass config directly
                return await self._execute_playwright_decision(state, step_config)
            elif step_config and step_config.get("method") == "stagehand":
                # Stagehand step - direct execution, no LLM decision (Stagehand has built-in LLM)
                return await self._execute_stagehand_decision(state, step_config)
            elif step_config and step_config.get("method") == "hitl":
                # HITL step - skip LLM, pass config directly
                return await self._execute_hitl_decision(state, step_config)
            elif step_config and step_config.get("action") == "end":
                # END step - skip LLM, just mark as complete
                return await self._execute_end_decision(state, step_config)

        # Prepare context for the LLM
        messages = state.get("messages", [])
        sop = state.get("sop", "")
        current_action = state.get("current_action")
        action_result = state.get("action_result")
        iteration_count = state.get("iteration_count", 0)

        # Get current step from state (set by previous iteration or initialized to 0)
        current_step_idx = state.get("current_step_idx", 0)
        consecutive_failures = state.get("consecutive_failures", 0)

        # Get YAML steps early so we can use them for step advancement logic
        yaml_steps = self.complexity_analyzer.yaml_steps

        # Validate yaml_steps is a list
        if yaml_steps is not None and not isinstance(yaml_steps, list):
            logger.warning(
                "decision.invalid_yaml_steps_type",
                component="decision",
                yaml_steps_type=type(yaml_steps),
            )
            yaml_steps = None

        # Check if previous action succeeded and we should advance to next step
        if iteration_count > 0 and action_result:
            status = action_result.get("status", "unknown")

            if status == "success":
                # Action succeeded - reset failure counter
                consecutive_failures = 0

                # Simple strategy: advance step after every successful action
                # This assumes each step = 1 action in YAML SOP
                if yaml_steps and current_step_idx < len(yaml_steps) - 1:
                    # Get the action we just executed (check result first, then stagehand_inputs for legacy)
                    actual_action = action_result.get("action_type", "").lower()
                    if not actual_action:
                        stagehand_inputs = state.get("stagehand_inputs", {})
                        actual_action = stagehand_inputs.get("action_type", "").lower()

                    # Safe array access with bounds check
                    if 0 <= current_step_idx < len(yaml_steps):
                        current_step_data = yaml_steps[current_step_idx]
                        expected_action = current_step_data.get("action", "").lower()

                    # If we executed an action matching current step, advance
                    # Match: goto->goto, act->act, observe->observe, end->end
                    if actual_action == expected_action:
                        current_step_idx = current_step_idx + 1
                        logger.info(
                            "decision.advance_step",
                            component="decision",
                            completed_step=current_step_idx - 1,
                            next_step=current_step_idx,
                        )
                    else:
                        logger.warning(
                            "decision.action_mismatch",
                            component="decision",
                            actual_action=actual_action,
                            expected_action=expected_action,
                        )

            elif status == "error":
                # Action failed - increment failure counter
                consecutive_failures += 1
                AgentLogger.error(f"Action failed: {action_result.get('message', 'Unknown error')}")

        # Determine current step description
        if yaml_steps and len(yaml_steps) > 0:
            total_steps = len(yaml_steps)
            # Keep step index within bounds (handle edge case of empty list)
            current_step_idx = min(current_step_idx, max(0, total_steps - 1))

            # Safe array access
            if 0 <= current_step_idx < len(yaml_steps):
                current_step_data = yaml_steps[current_step_idx]
                current_step_desc = f"[{current_step_data.get('action', 'act').upper()}] {current_step_data.get('description', '')}"
            else:
                current_step_desc = "Invalid step index"
        else:
            steps = self.complexity_analyzer._parse_sop_steps(sop)
            total_steps = len(steps) if steps else 5
            # Ensure total_steps is at least 1 to avoid -1 index
            total_steps = max(1, total_steps)
            current_step_idx = min(current_step_idx, total_steps - 1)
            current_step_desc = (
                steps[current_step_idx]
                if steps and 0 <= current_step_idx < len(steps)
                else "Processing..."
            )

        # Update state with current step
        state["current_step_idx"] = current_step_idx
        state["consecutive_failures"] = consecutive_failures

        # Show current step
        AgentLogger.step(current_step_idx + 1, total_steps, current_step_desc)
        AgentLogger.analyzing_step()

        # Take a screenshot of the current page
        screenshot_base64 = await self._capture_screenshot(page)

        # Build the prompt with screenshot context
        prompt = self.llm_client.build_decision_prompt(
            sop, iteration_count, self.max_iterations, current_action, action_result
        )

        # Determine which model to use based on task complexity
        should_use_computer_use = False
        model_reason = ""
        if self.llm_client.enable_dynamic_switching:
            should_use_computer_use, model_reason = (
                self.complexity_analyzer.should_use_computer_use(
                    sop=sop,
                    current_step_idx=current_step_idx,
                    consecutive_failures=consecutive_failures,
                    current_action=current_action,
                    action_result=action_result,
                )
            )

        # Get decision from LLM
        try:
            # PHASE 4: Use structured output for Flash model decisions (not Computer Use)
            # Computer Use model needs its tools, so we keep the standard flow for it
            use_structured = (
                not should_use_computer_use and self.llm_client.enable_dynamic_switching
            )

            if use_structured:
                # NEW: Structured output with confidence/risk scoring
                logger.info("decision.structured_output", component="decision")
                result = await self.llm_client.make_structured_decision(prompt, screenshot_base64)

                decision = result["decision"]

                # Enhanced logging with confidence and risk
                logger.info(
                    "decision.reasoning",
                    component="decision",
                    reasoning=decision["reasoning"],
                    confidence=decision["confidence"],
                    risk_level=decision["risk_level"],
                )

                if decision.get("alternatives"):
                    for alt in decision["alternatives"]:
                        logger.info(
                            "decision.alternative",
                            component="decision",
                            alternative_action=alt["action"],
                            reason_not_chosen=alt["reason_not_chosen"],
                        )

                # Save structured decision to database
                if self.repository and state.get("run_id"):
                    try:
                        tokens = result.get("tokens", {})
                        cost_usd = self._calculate_cost(tokens)

                        decision_id = await self.repository.save_llm_decision(
                            run_id=state["run_id"],
                            step_index=current_step_idx,
                            decision_type="agent_decision",
                            decision_data=decision,
                            model_used=result.get("model", "gemini-2.5-flash"),
                            tokens_input=tokens.get("input"),
                            tokens_output=tokens.get("output"),
                            cost_usd=cost_usd,
                            duration_ms=result.get("duration_ms"),
                            prompt_summary=prompt[:200],
                        )
                        logger.debug(
                            "decision.saved_to_db",
                            component="decision",
                            decision_id=decision_id,
                        )
                    except Exception as e:
                        logger.warning(
                            "decision.db_save_failed",
                            component="decision",
                            error=str(e),
                        )

                # Convert structured decision to standard format for state
                decision = {
                    "action_type": self._extract_action_type(decision["chosen_action"]),
                    "description": decision["chosen_action"],
                    "reasoning": decision["reasoning"],
                    "action_text": self._extract_action_text(decision["chosen_action"]),
                    "confidence": decision["confidence"],
                    "risk_level": decision["risk_level"],
                }

                result["status"] = "success"
                result["parsed_decision"] = decision
                result["model_used"] = "flash"

            else:
                # Use LLM client to make decision with intelligent model selection
                result = await self.llm_client.make_decision(
                    prompt,
                    screenshot_base64,
                    use_computer_use=should_use_computer_use,
                    model_reason=model_reason,
                )

            if result["status"] == "success":
                decision = result["parsed_decision"]

                # Show which model was used (only for non-structured output)
                if not use_structured:
                    model_used = result.get("model_used", "unknown")
                    model_reason = result.get("model_reason", "")
                    if model_used == "computer-use":
                        AgentLogger.model_switch("computer-use", model_reason)
                    elif model_used == "flash":
                        AgentLogger.model_switch("flash", model_reason)

                    # Show reasoning
                    reasoning = decision.get("reasoning", "")
                    AgentLogger.reasoning(reasoning)

                # Log token usage if available
                token_usage = result.get("token_usage")
                if token_usage:
                    AgentLogger.tokens(
                        token_usage.prompt_tokens,
                        token_usage.completion_tokens,
                        token_usage.total_tokens,
                        token_usage.cost_usd,
                        source="agent",
                    )

                # Update state
                state["current_action"] = decision.get("description", "")
                state["stagehand_inputs"] = {
                    "action_type": decision.get("action_type", ""),
                    "action_text": decision.get("action_text", ""),
                }
                state["iteration_count"] = iteration_count + 1

                # Add to messages
                messages.append(
                    {
                        "role": "assistant",
                        "content": f"Decision: {decision.get('description', '')}",
                        "action": decision,
                    }
                )
                state["messages"] = messages

                logger.info(
                    "decision.made",
                    component="decision",
                    description=decision.get("description", ""),
                )
            else:
                logger.error(
                    "decision.llm_error",
                    component="decision",
                    error=result.get("error", "Unknown error"),
                )
                state["current_action"] = "Error in decision making"
                state["stagehand_inputs"] = {"action_type": "", "action_text": ""}
                state["iteration_count"] = iteration_count + 1

        except Exception as e:
            logger.error("decision.node_error", component="decision", error=str(e), handled=False)
            state["current_action"] = "Error in decision making"
            state["stagehand_inputs"] = {"action_type": "", "action_text": ""}
            state["iteration_count"] = iteration_count + 1

        return state

    async def _capture_screenshot(self, page) -> Optional[str]:
        """Capture a screenshot of the current page."""
        screenshot_base64 = None
        try:
            if page and hasattr(page, "current_page") and page.current_page:
                logger.info("decision.screenshot_taking", component="decision")
                screenshot_bytes = await page.current_page.screenshot()
                screenshot_base64 = ScreenshotUtils.encode_to_base64(screenshot_bytes)
                logger.info("decision.screenshot_captured", component="decision")
            else:
                logger.warning("decision.screenshot_no_page", component="decision")
        except Exception as e:
            logger.error(
                "decision.screenshot_failed", component="decision", error=str(e), handled=False
            )

        return screenshot_base64

    async def _execute_playwright_decision(
        self, state: AgentState, step_config: Dict[str, Any]
    ) -> AgentState:
        """
        Execute decision for a Playwright step (skips LLM).

        Args:
            state: Current agent state
            step_config: Step configuration from YAML

        Returns:
            Updated state with Playwright action
        """
        action = step_config.get("action", "")
        description = step_config.get("description", "")
        iteration_count = state.get("iteration_count", 0)

        logger.info("decision.playwright_step", component="decision")
        logger.info(
            "decision.action",
            component="decision",
            action=action.upper(),
            description=description,
        )

        # Update state with Playwright action (no LLM reasoning needed)
        state["current_action"] = description
        state["stagehand_inputs"] = {
            "action_type": action,
            "action_text": description,
            "step_config": step_config,  # Pass full config to tool node
        }
        state["iteration_count"] = iteration_count + 1

        # Add to messages with step_config for tool node
        messages = state.get("messages", [])
        messages.append(
            {
                "role": "assistant",
                "content": f"Playwright Action: {description}",
                "action": {
                    "action_type": action,
                    "description": description,
                    "reasoning": "Direct Playwright action (0 LLM calls)",
                    "method": "playwright",
                },
                "step_config": step_config,  # Pass config to tool node
            }
        )
        state["messages"] = messages

        return state

    async def _execute_stagehand_decision(
        self, state: AgentState, step_config: Dict[str, Any]
    ) -> AgentState:
        """
        Execute decision for a Stagehand step (direct execution, no LLM decision).

        Args:
            state: Current agent state
            step_config: Step configuration from YAML

        Returns:
            Updated state with Stagehand action
        """
        action = step_config.get("action", "")
        description = step_config.get("description", "")
        instruction = step_config.get("instruction", description)
        iteration_count = state.get("iteration_count", 0)

        logger.info("decision.stagehand_step", component="decision", action=action)
        logger.info(
            "decision.action",
            component="decision",
            action=action.upper(),
            description=description,
        )

        # Update state
        state["current_action"] = description
        state["iteration_count"] = iteration_count + 1

        # Set stagehand inputs based on action type
        if action == "extract":
            state["stagehand_inputs"] = {
                "action_type": "extract",
                "action_text": instruction,
                "step_config": step_config,
            }
        elif action == "act":
            state["stagehand_inputs"] = {
                "action_type": "act",
                "action_text": instruction,
                "step_config": step_config,
            }
        elif action == "observe":
            state["stagehand_inputs"] = {
                "action_type": "observe",
                "action_text": instruction,
                "step_config": step_config,
            }
        else:
            # Fallback
            state["stagehand_inputs"] = {
                "action_type": action,
                "action_text": instruction,
                "step_config": step_config,
            }

        # Add to messages with step_config for tool node
        messages = state.get("messages", [])
        messages.append(
            {
                "role": "assistant",
                "content": f"Stagehand action: {description}",
                "action": {
                    "action_type": action,
                    "action_text": instruction,
                    "description": description,
                    "reasoning": f"Direct Stagehand {action} (Stagehand LLM - may use Computer Use model)",
                    "method": "stagehand",
                },
                "step_config": step_config,  # Pass config to tool node
            }
        )
        state["messages"] = messages

        return state

    async def _execute_hitl_decision(
        self, state: AgentState, step_config: Dict[str, Any]
    ) -> AgentState:
        """
        Execute decision for a HITL step (skips LLM).

        Args:
            state: Current agent state
            step_config: Step configuration from YAML

        Returns:
            Updated state with HITL action
        """
        action = step_config.get("action", "")
        description = step_config.get("description", "")
        iteration_count = state.get("iteration_count", 0)

        logger.info("decision.hitl_step", component="decision")
        logger.info(
            "decision.action",
            component="decision",
            action=action.upper(),
            description=description,
        )

        # Update state with HITL action
        state["current_action"] = description
        state["stagehand_inputs"] = {
            "action_type": action,
            "action_text": description,
            "step_config": step_config,
        }
        state["iteration_count"] = iteration_count + 1

        # Add to messages with step_config for tool node
        messages = state.get("messages", [])
        messages.append(
            {
                "role": "assistant",
                "content": f"HITL Action: {description}",
                "action": {
                    "action_type": action,
                    "description": description,
                    "reasoning": "Human-in-the-loop action",
                    "method": "hitl",
                },
                "step_config": step_config,  # Pass config to tool node
            }
        )
        state["messages"] = messages

        return state

    async def _execute_end_decision(
        self, state: AgentState, step_config: Dict[str, Any]
    ) -> AgentState:
        """
        Execute decision for an END step (skips LLM - deterministic completion).

        Args:
            state: Current agent state
            step_config: Step configuration from YAML

        Returns:
            Updated state with END action set
        """
        description = step_config.get("description", "Automation completed")
        action = step_config.get("action", "end")

        logger.info("decision.end_step", component="decision")
        logger.info(
            "decision.action",
            component="decision",
            action=action.upper(),
            description=description,
        )

        # Get current state values
        iteration_count = state.get("iteration_count", 0)
        consecutive_failures = state.get("consecutive_failures", 0)

        # Reset failure count (end is always success)
        consecutive_failures = 0
        state["consecutive_failures"] = consecutive_failures

        # Update state with END action (no LLM reasoning needed)
        state["current_action"] = description
        state["stagehand_inputs"] = {
            "action_type": "end",
            "action_text": description,
            "step_config": step_config,  # Pass full config to tool node
        }
        state["iteration_count"] = iteration_count + 1

        # Add to messages with step_config for tool node
        messages = state.get("messages", [])
        messages.append(
            {
                "role": "assistant",
                "content": f"END Action: {description}",
                "action": {
                    "action_type": "end",
                    "description": description,
                    "reasoning": "Deterministic END action (no LLM call)",
                    "method": "end",
                },
                "step_config": step_config,  # Pass config to tool node
            }
        )
        state["messages"] = messages

        return state

    # ========================================================================
    # Helper Methods for Structured Output (Phase 4)
    # ========================================================================

    def _calculate_cost(self, tokens: dict) -> float:
        """Calculate cost from token usage (Gemini Flash pricing)."""
        if not tokens:
            return 0.0

        input_tokens = tokens.get("input", 0)
        output_tokens = tokens.get("output", 0)

        # Gemini Flash pricing (per 1M tokens): Input=$0.15, Output=$0.60
        input_cost = (input_tokens * 0.15) / 1_000_000
        output_cost = (output_tokens * 0.60) / 1_000_000

        return input_cost + output_cost

    def _extract_action_type(self, chosen_action: str) -> str:
        """Extract action type from chosen_action string."""
        action_lower = chosen_action.lower()

        if any(word in action_lower for word in ["navigate", "goto", "go to", "visit", "open"]):
            return "goto"
        elif any(word in action_lower for word in ["extract", "scrape", "get data", "retrieve"]):
            return "extract"
        elif any(word in action_lower for word in ["observe", "check", "verify", "find"]):
            return "observe"
        elif any(word in action_lower for word in ["end", "finish", "complete", "done"]):
            return "end"
        else:
            return "act"

    def _extract_action_text(self, chosen_action: str) -> str:
        """Extract action text from chosen_action string."""
        action_lower = chosen_action.lower()

        # For navigation, try to extract URL
        if any(word in action_lower for word in ["navigate", "goto", "go to", "visit", "open"]):
            words = chosen_action.split()
            for word in words:
                if "http" in word or ("." in word and "/" in word):
                    return word
            return chosen_action

        # For all other actions, return the full text
        return chosen_action
