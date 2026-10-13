"""
Tool node implementation for the Browser Auto agent.

This module contains the tool node logic that executes browser actions using Stagehand.
"""

import os
import asyncio
from typing import Dict, Any, Optional

import structlog

from ..core.state import AgentState
from ..integrations import StagehandClient
from ..utils import AgentLogger

logger = structlog.get_logger(__name__)


class ToolNode:
    """Tool node that executes browser actions using Stagehand."""

    def __init__(self, stagehand_client: StagehandClient):
        """
        Initialize the tool node.

        Args:
            stagehand_client: The Stagehand client for browser automation
        """
        self.stagehand_client = stagehand_client
        self.credentials_config = {}  # Will be set by agent with credentials from YAML
        self.params_config = {}  # Will be set by agent with runtime params

    async def execute(self, state: AgentState) -> AgentState:
        """
        Execute the tool node logic.
        Routes to Playwright, Stagehand, or HITL based on YAML step config.

        Args:
            state: Current agent state

        Returns:
            Updated state with action result
        """
        # Check for emergency HITL injection (from decision node)
        if state.get("requires_emergency_hitl"):
            logger.info("tool.emergency_hitl_detected", component="tool")

            # Get the emergency message
            hitl_message = state.get("emergency_hitl_message", "Press Enter to continue...")

            # Show message and wait for user. Redacted because this is the one
            # console path that never touches a logging handler, and the message
            # is built from SOP-supplied text.
            from .._compat import redact_text

            print(redact_text(str(hitl_message)))
            import asyncio

            await asyncio.get_event_loop().run_in_executor(None, input)

            # Clear the emergency HITL flags
            state["requires_emergency_hitl"] = False
            state["emergency_hitl_message"] = None

            logger.info("tool.hitl_completed", component="tool")

            # Mark that we need to retry the current step (don't advance)
            state["retry_current_step"] = True

            # Return a neutral result that tells decision node to retry the same step
            result = {
                "status": "hitl_completed",  # Special status to indicate HITL happened
                "action_type": "emergency_hitl",
                "description": "Emergency HITL - manual popup closure",
                "message": "Human manually resolved blocking popup - retry the same step",
            }
            state["action_result"] = result

            messages = state.get("messages", [])
            messages.append(
                {
                    "role": "user",
                    "content": "Emergency HITL completed - popup manually resolved, retrying same step",
                    "result": result,
                }
            )
            state["messages"] = messages

            return state

        stagehand_inputs = state.get("stagehand_inputs", {})

        # Check if we have step_config from decision node (new YAML-driven approach)
        step_config = stagehand_inputs.get("step_config")

        if step_config:
            # Check if step has a condition (for conditional execution)
            condition = step_config.get("condition")
            if condition:
                # Evaluate condition
                should_execute = self._evaluate_condition(condition)

                if not should_execute:
                    # Skip this step
                    logger.info(
                        "tool.skip_step",
                        component="tool",
                        description=step_config.get("description", "N/A"),
                    )
                    result = {
                        "status": "success",
                        "skipped": True,
                        "reason": f"Condition not met: {condition}",
                        "action_type": step_config.get("action", ""),
                        "description": step_config.get("description", ""),
                    }
                    # Ensure action_type is in the result for step advancement
                    if "action_type" not in result:
                        result["action_type"] = step_config.get("action", "")

                    # Store result and update state
                    state["action_result"] = result
                    messages = state.get("messages", [])
                    messages.append(
                        {
                            "role": "system",
                            "content": f"Skipped conditional step: {step_config.get('description', 'N/A')}",
                            "result": result,
                        }
                    )
                    state["messages"] = messages
                    return state

            # New YAML-driven execution
            result = await self._execute_yaml_step(step_config)
            # Ensure action_type is in the result for step advancement
            if "action_type" not in result:
                result["action_type"] = step_config.get("action", "")
        else:
            # Legacy execution (for backward compatibility with old SOPs)
            logger.info("tool.execute_legacy", component="tool")
            action_type = stagehand_inputs.get("action_type", "")
            action_text = stagehand_inputs.get("action_text", "")

            if action_text and action_type:
                AgentLogger.action(action_type, action_text)
                result = await self._execute_action(action_type, action_text)
                status = result.get("status", "unknown")
                AgentLogger.result(status)
                await self.stagehand_client.wait(2)
            else:
                AgentLogger.error("No action text or action type provided")
                result = {"status": "error", "message": "No action text or action type provided"}

        # Store result and update state
        state["action_result"] = result

        # Track the active window context so downstream nodes (e.g. self-healing)
        # screenshot the window the agent is actually working in, not the frozen
        # first page. A newly-opened window becomes active; otherwise an explicit
        # step context wins; otherwise leave the previous active context untouched.
        if isinstance(result, dict):
            new_window = result.get("new_window")
            closed_window = result.get("closed_window")
            step_context = (step_config or {}).get("context")
            if new_window:
                state["active_context"] = new_window
            elif closed_window:
                # The step closed its popup window; don't leave downstream nodes
                # (e.g. self-healing) pointing at a now-missing context — fall back
                # to the live page until an explicit context step runs.
                state["active_context"] = None
            elif step_context:
                state["active_context"] = step_context

        # Add executed action to messages
        messages = state.get("messages", [])
        action_type = result.get("action_type", "") or stagehand_inputs.get("action_type", "")
        action_desc = (
            result.get("description", "")
            or step_config.get("description", "")
            or stagehand_inputs.get("action_text", "")
        )

        messages.append(
            {
                "role": "user",
                "content": f"Action executed ({action_type}): {action_desc}",
                "result": result,
            }
        )
        state["messages"] = messages

        return state

    async def _execute_yaml_step(self, step_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a step based on YAML configuration.
        Routes to appropriate handler based on 'method' field.

        Args:
            step_config: Step configuration from YAML

        Returns:
            Result dictionary
        """
        action = step_config.get("action", "")
        description = step_config.get("description", "")
        method = step_config.get("method", "stagehand")  # Default to stagehand

        try:
            # Check for END action first (no method required, deterministic)
            if action == "end":
                logger.info("tool.execute_end", component="tool")
                AgentLogger.action(action, description)
                result = {"status": "success", "action_type": "end", "description": description}
            else:
                logger.info("tool.execute", component="tool", action=action.upper(), method=method)
                AgentLogger.action(action, description)

                # Route based on method
                if method == "playwright":
                    result = await self._execute_playwright_step(step_config)
                elif method == "stagehand":
                    result = await self._execute_stagehand_step(step_config)
                elif method == "hitl":
                    result = await self._execute_hitl_step(step_config)
                else:
                    result = {"status": "error", "message": f"Unknown method: {method}"}

            # Log result
            status = result.get("status", "unknown")
            AgentLogger.result(status)

            # Apply delay if specified
            delay_after = step_config.get("delay_after", 0)
            if delay_after > 0:
                await self.stagehand_client.wait(delay_after)

            return result

        except Exception as e:
            AgentLogger.error(f"Error executing step: {str(e)}", handled=False)
            return {"status": "error", "message": str(e), "action": action}

    async def _execute_playwright_step(self, step_config: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a Playwright step (0 LLM calls)."""
        action = step_config.get("action", "")

        if action == "goto":
            # Interpolate {params.x} so URLs can carry runtime values (e.g. a
            # checklist search filter ?mawb={params.mawb_no}), matching how
            # selectors are interpolated below.
            url = self._interpolate_params(step_config.get("url", ""))
            wait_for = step_config.get(
                "wait_for", "load"
            )  # Default to 'load' for better reliability
            return await self.stagehand_client.navigate_to(url, wait_for=wait_for)

        elif action == "fill":
            selector = step_config.get("selector", "")
            value = self._resolve_value(step_config)
            context = step_config.get("context")
            iframe = step_config.get("iframe")

            # Validate selector and value
            if not selector:
                return {"status": "error", "message": "Empty selector provided for fill action"}
            if not value:
                logger.warning("tool.fill_empty_value", component="tool", selector=selector)

            return await self.stagehand_client.fill_field(selector, value, context, iframe)

        elif action == "type":
            selector = step_config.get("selector", "")
            value = self._resolve_value(step_config)
            context = step_config.get("context")
            iframe = step_config.get("iframe")
            delay = step_config.get("delay", 100)  # Default 100ms between keystrokes

            # Validate selector and value
            if not selector:
                return {"status": "error", "message": "Empty selector provided for type action"}
            if not value:
                logger.warning("tool.type_empty_value", component="tool", selector=selector)

            return await self.stagehand_client.type_field(selector, value, context, iframe, delay)

        elif action == "upload_file":
            selector = step_config.get("selector", "")
            context = step_config.get("context")
            iframe = step_config.get("iframe")

            # Resolve file path from params or direct value
            file_path = step_config.get("file_path")
            file_path_from = step_config.get("file_path_from")

            if file_path_from and file_path_from.startswith("params."):
                param_key = file_path_from.replace("params.", "")
                file_path = self.params_config.get(param_key)

            # Validate inputs
            if not selector:
                return {
                    "status": "error",
                    "message": "Empty selector provided for upload_file action",
                }
            if not file_path:
                return {
                    "status": "error",
                    "message": "No file path provided for upload_file action",
                }
            if not os.path.exists(file_path):
                return {"status": "error", "message": f"File not found: {file_path}"}

            logger.info("tool.upload_file", component="tool", file=file_path, selector=selector)

            # Use Playwright's set_input_files for file upload
            result = await self.stagehand_client.upload_file(
                selector=selector, file_path=file_path, context=context, iframe=iframe
            )

            if result.get("status") == "success":
                logger.info(
                    "tool.upload_file_success",
                    component="tool",
                    file=os.path.basename(file_path),
                )
            else:
                logger.error(
                    "tool.upload_file_failed",
                    component="tool",
                    error=result.get("message"),
                )

            return result

        elif action == "click":
            selector = step_config.get("selector", "")
            context = step_config.get("context")
            force_click = step_config.get("force_click", False)
            wait_for_new_window = step_config.get("wait_for_new_window", False)
            new_window_name = step_config.get("new_window_name")
            iframe = step_config.get("iframe")
            prep_script = step_config.get("prep_script")

            # Validate selector
            if not selector:
                return {"status": "error", "message": "Empty selector provided for click action"}

            result = await self.stagehand_client.click_element(
                selector=selector,
                context=context,
                force_click=force_click,
                wait_for_new_window=wait_for_new_window,
                new_window_name=new_window_name,
                iframe=iframe,
                prep_script=prep_script,
            )

            # Handle wait_for after click
            if "wait_for" in step_config:
                page = self.stagehand_client.get_page_context(context)
                wait_state = step_config["wait_for"]

                # Support multiple wait states: domcontentloaded, load, networkidle
                if wait_state in ["load", "domcontentloaded", "networkidle"]:
                    await page.wait_for_load_state(wait_state, timeout=30000)
                    logger.info("tool.wait_load_state", component="tool", load_state=wait_state)
                else:
                    logger.warning(
                        "tool.wait_load_state_unknown",
                        component="tool",
                        load_state=wait_state,
                    )
                    await page.wait_for_load_state("domcontentloaded", timeout=30000)

            return result

        elif action == "dblclick":
            selector = self._interpolate_params(step_config.get("selector", ""))
            context = step_config.get("context")
            wait_for_new_window = step_config.get("wait_for_new_window", False)
            new_window_name = step_config.get("new_window_name")
            iframe = step_config.get("iframe")
            closes_window = step_config.get("closes_window", False)

            if not selector:
                return {"status": "error", "message": "Empty selector provided for dblclick action"}

            logger.info(
                "tool.dblclick",
                component="tool",
                selector=selector,
                iframe=iframe,
                wait_for_new_window=wait_for_new_window,
                closes_window=closes_window,
            )
            return await self.stagehand_client.dblclick_element(
                selector=selector,
                context=context,
                wait_for_new_window=wait_for_new_window,
                new_window_name=new_window_name,
                iframe=iframe,
                closes_window=closes_window,
            )

        elif action == "wait_for_selector":
            selector = self._interpolate_params(step_config.get("selector", ""))
            timeout = step_config.get("timeout", 10000)
            context = step_config.get("context")
            iframe = step_config.get("iframe")
            state = step_config.get("state", "visible")

            # Validate selector
            if not selector:
                return {
                    "status": "error",
                    "message": "Empty selector provided for wait_for_selector action",
                }

            return await self.stagehand_client.wait_for_element(
                selector, timeout, context, iframe, state
            )

        elif action == "wait":
            duration = step_config.get("duration", 1000)  # milliseconds
            context = step_config.get("context")

            # Convert to seconds for asyncio.sleep
            duration_seconds = duration / 1000.0
            await asyncio.sleep(duration_seconds)

            return {
                "status": "success",
                "action_type": "wait",
                "duration": duration,
                "context": context or "main",
            }

        elif action == "wait_for_load_state":
            state = step_config.get(
                "state", "networkidle"
            )  # networkidle, load, or domcontentloaded
            context = step_config.get("context")
            timeout = step_config.get("timeout", 30000)  # milliseconds
            # Opt-in peak-hour scaling (scale_timeout: true on the step): the
            # invoice-Update networkidle wait must survive a ~44s+ save postback
            # under 2-3pm load. Scoped to opt-in steps so SOP_TIMEOUT_SCALE does
            # not broaden failure latency across every wait_for_load_state (incl.
            # Staredi/IceGate) — a stuck base-60s wait would otherwise take up to
            # ~30min to fail at the max scale under the 3-retry policy.
            if step_config.get("scale_timeout"):
                timeout = self.stagehand_client._scaled(timeout)

            try:
                page = self.stagehand_client.get_page_context(context)

                logger.info(
                    "playwright.wait_load_state",
                    component="playwright",
                    load_state=state,
                    timeout_ms=timeout,
                )
                await page.wait_for_load_state(state, timeout=timeout)
                logger.info("tool.wait_load_state_reached", component="tool", load_state=state)

                return {
                    "status": "success",
                    "action_type": "wait_for_load_state",
                    "state": state,
                    "context": context or "main",
                }
            except Exception as e:
                logger.error(
                    "tool.wait_load_state_failed", component="tool", error=str(e), handled=False
                )
                return {"status": "error", "message": str(e), "action_type": "wait_for_load_state"}

        elif action == "press":
            key = step_config.get("key", "")
            selector = step_config.get("selector")  # Optional: press key on specific element
            context = step_config.get("context")
            iframe = step_config.get("iframe")

            if not key:
                return {"status": "error", "message": "Empty key provided for press action"}

            return await self.stagehand_client.press_key(key, selector, context, iframe)

        elif action == "execute_script":
            script = step_config.get("script", "")
            script_args = step_config.get("script_args")
            extract_result = bool(step_config.get("extract_result"))
            context = step_config.get("context")
            iframe = step_config.get("iframe")
            wait_for_new_window = step_config.get("wait_for_new_window", False)
            new_window_name = step_config.get("new_window_name")

            logger.info(
                "tool.execute_script_start",
                component="tool",
                context=context,
                iframe=iframe,
            )

            if not script:
                return {
                    "status": "error",
                    "message": "Empty script provided for execute_script action",
                }

            try:
                page = self.stagehand_client.get_page_context(context)
                logger.info("tool.got_page_context", component="tool", page=page)

                # When script_args is present the script must be a function expression
                # (e.g. "(args) => {...}") — Playwright requires that to pass an argument.
                script_arg_obj = self._resolve_script_args(script_args) if script_args else None

                # Capture page count before script so we can detect new window after
                initial_pages = 0
                if wait_for_new_window and self.stagehand_client.context:
                    ctx_pages = self.stagehand_client.context.pages
                    if asyncio.iscoroutine(ctx_pages):
                        ctx_pages = await ctx_pages
                    elif callable(ctx_pages):
                        ctx_pages = ctx_pages()
                        if asyncio.iscoroutine(ctx_pages):
                            ctx_pages = await ctx_pages
                    initial_pages = len(ctx_pages)
                    logger.info("tool.initial_page_count", component="tool", count=initial_pages)

                if iframe:
                    logger.info(
                        "playwright.execute_script_iframe",
                        component="playwright",
                        iframe=iframe,
                    )
                    frame_element = await page.query_selector(iframe)
                    logger.info(
                        "tool.query_selector_result",
                        component="tool",
                        frame_element=frame_element,
                    )

                    if frame_element:
                        frame = await frame_element.content_frame()
                        logger.info("tool.content_frame_result", component="tool", frame=frame)

                        if frame:
                            logger.info("tool.evaluate_script_frame", component="tool")
                            if script_arg_obj is not None:
                                result = await frame.evaluate(script, script_arg_obj)
                            else:
                                result = await frame.evaluate(script)
                            logger.info("tool.evaluate_returned", component="tool", result=result)
                        else:
                            logger.error(
                                "tool.content_frame_missing",
                                component="tool",
                                iframe=iframe,
                            )
                            return {
                                "status": "error",
                                "message": f"Could not get content frame for iframe: {iframe}",
                            }
                    else:
                        logger.error("tool.iframe_not_found", component="tool", iframe=iframe)
                        return {"status": "error", "message": f"Iframe not found: {iframe}"}
                else:
                    logger.info("playwright.execute_script_page", component="playwright")
                    if script_arg_obj is not None:
                        result = await page.evaluate(script, script_arg_obj)
                    else:
                        result = await page.evaluate(script)
                    logger.info("tool.page_evaluate_returned", component="tool", result=result)

                logger.info("tool.execute_script_success", component="tool")

                # Log the result for visibility (always log, even if None/empty)
                import json

                logger.info(
                    "tool.script_result",
                    component="tool",
                    result=json.dumps(result, indent=2) if result else repr(result),
                )

                # A script may return {"set_params": {...}} to drive later
                # `condition: params.X` steps (see _apply_script_set_params).
                if isinstance(result, dict):
                    self._apply_script_set_params(result)

                # Handle wait_for_new_window: poll for the popup opened by the script
                if wait_for_new_window:
                    logger.info(
                        "tool.wait_new_window",
                        component="tool",
                        initial_pages=initial_pages,
                    )
                    window_name = await self.stagehand_client._wait_for_and_register_window(
                        initial_pages, new_window_name
                    )
                    response = {
                        "status": "success",
                        "action_type": "execute_script",
                        "result": result,
                        "new_window": window_name,
                    }
                    if extract_result and result is not None:
                        response["extraction"] = result
                    return response

                # Explicit extraction flag: surface the result to extraction_data
                # without rewriting action_type (keeps step advancement semantics).
                if extract_result and result is not None:
                    return {
                        "status": "success",
                        "action_type": "execute_script",
                        "extraction": result,
                        "result": result,
                    }

                # Check if this is an extraction script (by description keyword)
                description = step_config.get("description", "").lower()
                is_extraction = "extract" in description

                # Normalize output format for extraction scripts
                if is_extraction and result:
                    return {
                        "status": "success",
                        "action_type": "extract",  # Use 'extract' for compatibility with agent.py
                        "extraction": result,  # Use 'extraction' key for compatibility with extract steps
                        "result": result,  # Also keep 'result' for backwards compatibility
                    }
                else:
                    return {"status": "success", "action_type": "execute_script", "result": result}
            except Exception as e:
                logger.error(
                    "tool.execute_script_failed", component="tool", error=str(e), handled=False
                )
                import traceback

                logger.error(
                    "tool.traceback",
                    component="tool",
                    traceback=traceback.format_exc(),
                    handled=False,
                )
                return {"status": "error", "message": str(e), "action_type": "execute_script"}

        elif action == "download_file":
            selector = step_config.get("selector", "")
            context = step_config.get("context")
            iframe = step_config.get("iframe")

            save_path = step_config.get("save_path")
            save_path_from = step_config.get("save_path_from")
            if save_path_from and save_path_from.startswith("params."):
                save_path = self.params_config.get(save_path_from.replace("params.", ""))

            if not selector:
                return {"status": "error", "message": "Empty selector for download_file"}
            if not save_path:
                return {"status": "error", "message": "No save_path for download_file"}

            try:
                page = self.stagehand_client.get_page_context(context)
                os.makedirs(os.path.dirname(save_path), exist_ok=True)

                if self.stagehand_client.use_browserbase:
                    # Browserbase mode: download.save_as() is canceled in remote CDP sessions
                    # because the file lands in the remote browser, not the local filesystem.
                    # Click the trigger directly, then retrieve via Browserbase downloads API.
                    import httpx
                    import zipfile
                    import io as _io

                    session_id = self.stagehand_client.session_id
                    bb_api_key = os.environ.get("BROWSERBASE_API_KEY", "")
                    dl_url = f"https://api.browserbase.com/v1/sessions/{session_id}/downloads"
                    headers = {"x-bb-api-key": bb_api_key}

                    # Snapshot existing downloads before clicking so we can detect
                    # only the file added by this specific click (isolation guard).
                    pre_click_names: set[str] = set()
                    async with httpx.AsyncClient(timeout=15) as pre_http:
                        try:
                            pre_resp = await pre_http.get(dl_url, headers=headers)
                            if pre_resp.status_code == 200 and pre_resp.content:
                                with zipfile.ZipFile(_io.BytesIO(pre_resp.content)) as pre_zf:
                                    pre_click_names = set(pre_zf.namelist())
                        except Exception:
                            pass  # no prior downloads — baseline stays empty

                    if iframe:
                        frame_el = await page.query_selector(iframe)
                        frame = await frame_el.content_frame() if frame_el else None
                        if frame:
                            await frame.locator(selector).click()
                        else:
                            return {"status": "error", "message": f"Iframe not found: {iframe}"}
                    else:
                        await page.locator(selector).click()

                    file_content: Optional[bytes] = None
                    async with httpx.AsyncClient(timeout=30) as http_client:
                        for attempt in range(12):  # poll every 5s, max 60s total
                            await asyncio.sleep(5)
                            resp = await http_client.get(dl_url, headers=headers)
                            if resp.status_code != 200:
                                logger.info(
                                    "tool.browserbase_download_not_synced",
                                    component="tool",
                                    status_code=resp.status_code,
                                    attempt=attempt + 1,
                                )
                                continue
                            if not resp.content:
                                logger.info(
                                    "tool.browserbase_download_empty",
                                    component="tool",
                                    attempt=attempt + 1,
                                )
                                continue
                            try:
                                with zipfile.ZipFile(_io.BytesIO(resp.content)) as zf:
                                    new_names = [
                                        n for n in zf.namelist() if n not in pre_click_names
                                    ]
                                    if new_names:
                                        latest = max(
                                            new_names, key=lambda n: zf.getinfo(n).date_time
                                        )
                                        file_content = zf.read(latest)
                                        logger.info(
                                            "tool.browserbase_download_found",
                                            component="tool",
                                            file=latest,
                                        )
                                        break
                                    logger.info(
                                        "tool.browserbase_download_no_new_entries",
                                        component="tool",
                                        attempt=attempt + 1,
                                        zip_names=zf.namelist(),
                                    )
                            except zipfile.BadZipFile:
                                logger.info(
                                    "tool.browserbase_download_invalid_zip",
                                    component="tool",
                                    attempt=attempt + 1,
                                    bytes=len(resp.content),
                                )

                    if not file_content:
                        return {
                            "status": "error",
                            "message": "Browserbase download did not appear within 60s",
                            "action_type": "download_file",
                        }

                    with open(save_path, "wb") as fh:
                        fh.write(file_content)
                    logger.info(
                        "tool.download_file_browserbase",
                        component="tool",
                        file=save_path,
                    )

                else:
                    # Local mode: standard Playwright download interception
                    async with page.expect_download(timeout=60000) as download_info:
                        if iframe:
                            frame_el = await page.query_selector(iframe)
                            frame = await frame_el.content_frame() if frame_el else None
                            if frame:
                                await frame.locator(selector).click()
                            else:
                                return {"status": "error", "message": f"Iframe not found: {iframe}"}
                        else:
                            await page.locator(selector).click()

                    download = await download_info.value
                    await download.save_as(save_path)
                    logger.info("tool.download_file", component="tool", file=save_path)

                return {
                    "status": "success",
                    "action_type": "extract",
                    "extraction": {"downloaded_file_path": save_path},
                }
            except Exception as e:
                logger.error(
                    "tool.download_file_failed", component="tool", error=str(e), handled=False
                )
                return {"status": "error", "message": str(e), "action_type": "download_file"}

        elif action == "download_pdf_pages":
            context = step_config.get("context")

            save_dir = step_config.get("save_dir")
            save_dir_from = step_config.get("save_dir_from")
            if save_dir_from and save_dir_from.startswith("params."):
                save_dir = self.params_config.get(save_dir_from.replace("params.", ""), "")

            job_number = step_config.get("job_number")
            job_number_from = step_config.get("job_number_from")
            if job_number_from and job_number_from.startswith("params."):
                job_number = self.params_config.get(job_number_from.replace("params.", ""), "job")

            if not save_dir:
                return {
                    "status": "error",
                    "message": "save_dir not provided for download_pdf_pages",
                }

            safe_job_number = (job_number or "job").replace("/", "_")
            os.makedirs(save_dir, exist_ok=True)

            # Resilience tunables for the (otherwise terminal) PDF download.
            max_pset_attempts = 3
            min_pdf_bytes = 1024  # smaller than this is a truncated/empty body, not a checklist

            def _looks_like_pdf(data: bytes) -> bool:
                # A valid PDF starts with "%PDF-" AND ends with "%%EOF". The header
                # check rejects an HTML auth/error page returned with HTTP 200 (e.g.
                # session expired mid-download); the trailing-%%EOF check rejects a
                # truncated body that has the header but was cut off — either would
                # otherwise be saved as a .pdf and slip through to stitch/ERP upload.
                return (
                    len(data) >= min_pdf_bytes and data[:5] == b"%PDF-" and b"%%EOF" in data[-2048:]
                )

            try:
                page = self.stagehand_client.get_page_context(context)

                # ── Opener-based fetch (Browserbase-safe) ─────────────────────────
                # A popup that renders a PDF is unreadable on Browserbase (its url is
                # ":" and it never fires load), so run in the OPENER page and use the
                # URL window.open captured when Print fired. The opener is a normal
                # same-origin page, so fetch() + auth cookies work. Handles BOTH shapes:
                # small checklists are a single inline PDF; large ones are an HTML
                # page-set list (generatePageSet links) fetched per section.
                popup_url_var = step_config.get("popup_url_var")
                if popup_url_var:
                    import re as _re

                    base_url = None
                    for _ in range(20):
                        base_url = await page.evaluate(
                            "(v) => { const r = window[v]; return r ? new URL(r, location.href).href : null; }",
                            popup_url_var,
                        )
                        if base_url:
                            break
                        await asyncio.sleep(1)
                    # Require a real DocFldNavPDF URL — guards against a stale value
                    # left on the window var by a prior attempt (the hook also resets
                    # it and only stores DocFldNavPDF URLs, but defend here too).
                    if not base_url or "DocFldNavPDF" not in base_url:
                        return {
                            "status": "error",
                            "message": f"download_pdf_pages: print popup URL not captured on window.{popup_url_var} (got {base_url!r})",
                            "action_type": "download_pdf_pages",
                        }

                    async def _fetch_doc(url: str) -> tuple[str, bytes]:
                        res = await page.evaluate(
                            """async (url) => {
                                const resp = await fetch(url, { credentials: 'include' });
                                if (!resp.ok) throw new Error(`HTTP ${resp.status} ${resp.statusText}`);
                                const buf = await resp.arrayBuffer();
                                return { ct: resp.headers.get('content-type') || '',
                                         bytes: Array.from(new Uint8Array(buf)) };
                            }""",
                            url,
                        )
                        return res.get("ct", ""), bytes(res.get("bytes") or [])

                    opener_paths: list[str] = []
                    ct, data = await _fetch_doc(base_url)
                    if _looks_like_pdf(data):
                        pdf_path = os.path.join(save_dir, f"{safe_job_number}_checklist.pdf")
                        with open(pdf_path, "wb") as fh:
                            fh.write(data)
                        opener_paths.append(pdf_path)
                    else:
                        html = data.decode("utf-8", errors="ignore")
                        sets = sorted(
                            {int(m) for m in _re.findall(r"generatePageSet\(\s*(\d+)\s*\)", html)}
                        )
                        if not sets:
                            return {
                                "status": "error",
                                "message": f"download_pdf_pages: opener fetch returned neither a PDF nor page-set links (ct={ct!r}, bytes={len(data)}, url={base_url}).",
                                "action_type": "download_pdf_pages",
                            }
                        # Page sets must be 1..N contiguous; a gap means we'd save a
                        # partial checklist (and stitch/upload it) without noticing.
                        if sets != list(range(1, sets[-1] + 1)):
                            return {
                                "status": "error",
                                "message": f"download_pdf_pages: page-set links are non-contiguous {sets} — refusing to save a partial checklist.",
                                "action_type": "download_pdf_pages",
                            }
                        for n in sets:
                            _ct, pdata = await _fetch_doc(f"{base_url}&pset={n}")
                            if not _looks_like_pdf(pdata):
                                return {
                                    "status": "error",
                                    "message": f"download_pdf_pages: page-set {n}/{len(sets)} was not a PDF (bytes={len(pdata)}).",
                                    "action_type": "download_pdf_pages",
                                }
                            pdf_path = os.path.join(
                                save_dir, f"{safe_job_number}_checklist_{n}.pdf"
                            )
                            with open(pdf_path, "wb") as fh:
                                fh.write(pdata)
                            opener_paths.append(pdf_path)

                    logger.info(
                        "tool.checklist_downloaded",
                        component="tool",
                        count=len(opener_paths),
                        via="opener",
                        url=base_url,
                    )
                    return {
                        "status": "success",
                        "action_type": "extract",
                        "extraction": {"checklist_pdf_paths": opener_paths},
                    }

                # ── Legacy popup-based path ───────────────────────────────────────
                # The print popup is opened via window.open and navigates to
                # DocFldNavPDF.aspx; on remote browsers its url can read blank for a
                # few seconds after open. Wait for the real document URL first —
                # otherwise base_url is "about:blank" and every fetch below is useless.
                base_url = page.url
                for _ in range(60):
                    if "DocFldNavPDF" in (base_url or ""):
                        break
                    await asyncio.sleep(1)
                    base_url = page.url

                # The print popup renders the generatePageSet links asynchronously.
                # A 0 count almost always means they haven't appeared yet (not that
                # there are none), so poll briefly before deciding — otherwise we'd
                # fall through to page.pdf() and silently save a blank print-preview.
                page_set_count = 0
                for _ in range(10):
                    page_set_count = await page.evaluate(
                        "() => document.querySelectorAll('a[onclick*=\"generatePageSet\"]').length"
                    )
                    if page_set_count > 0:
                        break
                    await asyncio.sleep(1)

                logger.info(
                    "tool.checklist_pset_count",
                    component="tool",
                    count=page_set_count,
                    popup_url=page.url,
                )
                saved_paths = []

                if page_set_count == 0:
                    # No page-set links: LogiSys serves SMALL checklists (not "divided
                    # into sections") as a single inline PDF straight from
                    # DocFldNavPDF.aspx. Fetch that URL directly — auth cookies travel
                    # with credentials:'include'. page.pdf() would only screenshot the
                    # PDF-viewer chrome and emit a blank file, so it's a last resort.
                    pdf_path = os.path.join(save_dir, f"{safe_job_number}_checklist.pdf")
                    single_pdf: bytes | None = None
                    if "DocFldNavPDF" in (base_url or ""):
                        try:
                            single_list: list[int] = await page.evaluate(
                                """async (url) => {
                                    const resp = await fetch(url, { credentials: 'include' });
                                    if (!resp.ok) throw new Error(`HTTP ${resp.status} ${resp.statusText}`);
                                    const buf = await resp.arrayBuffer();
                                    return Array.from(new Uint8Array(buf));
                                }""",
                                base_url,
                            )
                            candidate = bytes(single_list)
                            if _looks_like_pdf(candidate):
                                single_pdf = candidate
                            else:
                                logger.warning(
                                    "tool.checklist_single_not_pdf",
                                    component="tool",
                                    bytes=len(candidate),
                                    head=repr(candidate[:8]),
                                )
                        except Exception as fe:
                            logger.warning(
                                "tool.checklist_single_fetch_failed",
                                component="tool",
                                error=str(fe),
                            )

                    if single_pdf is not None:
                        with open(pdf_path, "wb") as fh:
                            fh.write(single_pdf)
                        saved_paths.append(pdf_path)
                    else:
                        # Last resort: page.pdf() (headless only). Validate the output —
                        # on Browserbase/CDP it can emit a valid-but-empty PDF.
                        await page.pdf(path=pdf_path)
                        try:
                            with open(pdf_path, "rb") as fh:
                                head = fh.read(8)
                            size = os.path.getsize(pdf_path)
                        except OSError as oe:
                            return {
                                "status": "error",
                                "message": f"download_pdf_pages: could not read single-page fallback PDF: {oe}",
                                "action_type": "download_pdf_pages",
                            }
                        if head[:5] != b"%PDF-" or size < min_pdf_bytes:
                            return {
                                "status": "error",
                                "message": f"download_pdf_pages: no page-set links and DocFldNavPDF did not return a PDF (popup_url={base_url!r}, fallback size={size}, head={head!r}). The checklist export may be empty.",
                                "action_type": "download_pdf_pages",
                            }
                        saved_paths.append(pdf_path)
                else:
                    for n in range(1, page_set_count + 1):
                        pset_url = f"{base_url}&pset={n}"
                        pdf_path = os.path.join(save_dir, f"{safe_job_number}_checklist_{n}.pdf")
                        # The pset URL returns the PDF as Content-Disposition: attachment,
                        # so page.goto() aborts with "Download is starting". Fetch the bytes
                        # from within the page's JS context (auth cookies travel with
                        # credentials: 'include') and write them locally. A single transient
                        # fetch failure here is otherwise fatal to the whole run (SOP errors
                        # are NonRetriableError), so retry each page-set a few times.
                        pdf_bytes = None
                        last_err = None
                        for attempt in range(1, max_pset_attempts + 1):
                            try:
                                pdf_bytes_list: list[int] = await page.evaluate(
                                    """async (url) => {
                                        const resp = await fetch(url, { credentials: 'include' });
                                        if (!resp.ok) throw new Error(`HTTP ${resp.status} ${resp.statusText}`);
                                        const buf = await resp.arrayBuffer();
                                        return Array.from(new Uint8Array(buf));
                                    }""",
                                    pset_url,
                                )
                                candidate = bytes(pdf_bytes_list)
                                if not _looks_like_pdf(candidate):
                                    raise ValueError(
                                        f"response was not a valid PDF (len={len(candidate)}, head={candidate[:8]!r})"
                                    )
                                pdf_bytes = candidate
                                break
                            except Exception as fe:
                                last_err = fe
                                logger.warning(
                                    "tool.checklist_pset_retry",
                                    component="tool",
                                    pset=n,
                                    attempt=attempt,
                                    max_attempts=max_pset_attempts,
                                    error=str(fe),
                                )
                                if attempt < max_pset_attempts:
                                    await asyncio.sleep(2 * attempt)

                        if pdf_bytes is None:
                            return {
                                "status": "error",
                                "message": f"download_pdf_pages: page-set {n}/{page_set_count} failed after {max_pset_attempts} attempts: {last_err}",
                                "action_type": "download_pdf_pages",
                            }

                        with open(pdf_path, "wb") as fh:
                            fh.write(pdf_bytes)
                        saved_paths.append(pdf_path)
                        logger.info(
                            "tool.checklist_pset_saved",
                            component="tool",
                            pset=n,
                            file=pdf_path,
                            bytes=len(pdf_bytes),
                        )

                logger.info("tool.checklist_pdfs_saved", component="tool", files=saved_paths)
                return {
                    "status": "success",
                    "action_type": "extract",
                    "extraction": {"checklist_pdf_paths": saved_paths},
                }
            except Exception as e:
                logger.error(
                    "tool.download_pdf_pages_failed", component="tool", error=str(e), handled=False
                )
                return {"status": "error", "message": str(e), "action_type": "download_pdf_pages"}

        else:
            return {"status": "error", "message": f"Unknown Playwright action: {action}"}

    async def _execute_stagehand_step(self, step_config: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a Stagehand step (uses LLM)."""
        action = step_config.get("action", "")
        context = step_config.get("context")  # Get context (e.g., "dashboard")

        # Log context for debugging
        if context:
            logger.info("tool.window_context", component="tool", context=context)
        else:
            logger.info("tool.window_context_main", component="tool")

        if action == "act":
            instruction = step_config.get("instruction", step_config.get("description", ""))
            return await self.stagehand_client.perform_action(instruction, context)

        elif action == "extract":
            instruction = step_config.get("instruction", step_config.get("description", ""))
            schema = step_config.get("schema")  # v3: Extract JSON schema from SOP

            if not schema:
                logger.warning("tool.extract_no_schema", component="tool")
                logger.warning("tool.extract_add_schema_hint", component="tool")
                return {
                    "status": "error",
                    "message": "Schema is required for Stagehand v3 extract operations. Please add 'schema' field to the SOP step.",
                    "action_type": "extract",
                }

            logger.info("tool.extract_with_schema", component="tool")
            return await self.stagehand_client.extract_data(
                instruction=instruction,
                schema=schema,
                context=context,
                use_computer_use=False,  # v3: Use Flash model via API
            )

        elif action == "observe":
            instruction = step_config.get("instruction", step_config.get("description", ""))
            # Computer Use model is preferred for observation, but may fall back to Flash
            logger.info("tool.observe", component="tool")
            return await self.stagehand_client.observe_elements(
                instruction, context, use_computer_use=True
            )

        else:
            # Fallback: use instruction or description as action
            instruction = step_config.get("instruction", step_config.get("description", ""))
            return await self.stagehand_client.perform_action(instruction, context)

    async def _execute_hitl_step(self, step_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a human-in-the-loop step.

        stdin-based HITL is not supported in Inngest mode — it would block the
        worker thread indefinitely. Raise a RuntimeError so the Inngest step
        wrapper can surface it as a NonRetriableError and prompt an operator to
        resolve via POST /api/v1/hitl/resolve.
        """
        action = step_config.get("action", "")
        message = step_config.get("message", "Human input required")
        raise RuntimeError(
            f"HITL step '{action}' requires human intervention: {message}. "
            "Resolve via the HITL endpoint (POST /api/v1/hitl/resolve) with "
            "action='retry' or action='skip'."
        )

    def _evaluate_condition(self, condition: str) -> bool:
        """
        Evaluate a condition for conditional step execution.

        Currently supports:
        - params.field_name: Check if parameter exists and is truthy

        Args:
            condition: Condition string (e.g., "params.duty_structure_file_path")

        Returns:
            True if condition is met, False otherwise
        """
        if condition.startswith("params."):
            param_key = condition.replace("params.", "")
            param_value = self.params_config.get(param_key)

            # Check if param exists and is truthy (not None, not empty string, not empty list)
            if param_value:
                return True
            else:
                logger.info(
                    "tool.condition_not_met",
                    component="tool",
                    condition=condition,
                    value=param_value,
                )
                return False

        # Unknown condition format - default to True to avoid breaking existing SOPs
        logger.warning("tool.condition_unknown_format", component="tool", condition=condition)
        return True

    def _resolve_value(self, step_config: Dict[str, Any]) -> str:
        """
        Resolve value from step config.
        Handles 'value' (direct), 'value_from' (env var or params reference).

        Args:
            step_config: Step configuration

        Returns:
            Resolved value string

        Raises:
            ValueError: If credential or param reference is not found
        """
        # Direct value
        if "value" in step_config:
            return step_config["value"]

        # Value from reference (env var, credential, or runtime param)
        if "value_from" in step_config:
            ref = step_config["value_from"]

            # Handle runtime parameters (e.g., "params.erp_job_id")
            if ref.startswith("params."):
                param_key = ref.replace("params.", "")

                if param_key not in self.params_config:
                    raise ValueError(
                        f"Runtime parameter '{param_key}' not found. "
                        f"Available params: {list(self.params_config.keys())}"
                    )

                param_value = self.params_config.get(param_key, "")
                if not param_value:
                    raise ValueError(
                        f"Runtime parameter '{param_key}' is empty. "
                        f"This is required for action '{step_config.get('action', 'unknown')}'."
                    )

                return str(param_value)

            # Handle credentials (e.g., "credentials.username_env")
            if ref.startswith("credentials."):
                cred_key = ref.replace("credentials.", "")

                env_var_name = self.credentials_config.get(cred_key, "")

                if not env_var_name:
                    raise ValueError(
                        f"Credential key '{cred_key}' not found in credentials config. "
                        f"Available keys: {list(self.credentials_config.keys())}"
                    )

                env_value = os.getenv(env_var_name, "")
                if not env_value:
                    raise ValueError(
                        f"Environment variable '{env_var_name}' is not set. "
                        f"This credential is required for action '{step_config.get('action', 'unknown')}'."
                    )

                return env_value

            # Fallback: try ref as direct env var name
            env_value = os.getenv(ref, "")
            if env_value:
                return env_value
            else:
                raise ValueError(
                    f"Environment variable '{ref}' is not set. "
                    f"This is required for action '{step_config.get('action', 'unknown')}'."
                )

        return ""

    def set_credentials_config(self, credentials: Dict[str, str]):
        """
        Set credentials configuration from YAML.

        Args:
            credentials: Dictionary mapping credential keys to env var names
        """
        self.credentials_config = credentials

    def set_params_config(self, params: Dict[str, Any]):
        """
        Set runtime parameters configuration.
        This is called by the agent when executing an SOP with params.

        Injects ``sop_timeout_scale`` so SOP-level JS guards (e.g. the checklist
        save-confirmation poll) can scale their own wall-clock budgets for peak,
        mirroring the engine-side timeout scaling in stagehand_client. Copies the
        caller's dict so the injection never mutates it; an explicit param wins.

        Args:
            params: Runtime parameters dictionary
        """
        self.params_config = dict(params)
        self.params_config.setdefault(
            "sop_timeout_scale", getattr(self.stagehand_client, "timeout_scale", 1.0)
        )

    def _resolve_script_args(self, script_args: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve a script_args mapping for execute_script.

        String values prefixed with "params." are looked up in params_config
        (raising if absent, so missing data fails loudly); anything else is
        passed through as a literal. The resolved dict is handed to Playwright's
        evaluate() as its argument object — no string interpolation into JS.
        """
        resolved: Dict[str, Any] = {}
        for name, ref in script_args.items():
            if isinstance(ref, str) and ref.startswith("params."):
                param_key = ref.replace("params.", "", 1)
                if param_key not in self.params_config:
                    raise ValueError(
                        f"Runtime parameter '{param_key}' not found for script_args. "
                        f"Available params: {list(self.params_config.keys())}"
                    )
                resolved[name] = self.params_config.get(param_key)
            else:
                resolved[name] = ref
        return resolved

    def _interpolate_params(self, text: str) -> str:
        """Substitute {params.key} placeholders with values from params_config."""
        import re

        def replacer(match):
            key = match.group(1)
            value = self.params_config.get(key)
            return str(value) if value is not None else match.group(0)

        return re.sub(r"\{params\.([^}]+)\}", replacer, text)

    def _apply_script_set_params(self, result: Dict[str, Any]) -> None:
        """Merge a script's ``{"set_params": {...}}`` into params_config in place.

        Lets an execute_script step set runtime params that later
        ``condition: params.X`` steps read — e.g. flipping is_single_hawb /
        is_multiple_hawb off so the HAWB-creation steps skip themselves when a
        MAWB is already filed. Always strips the key so it never leaks into
        extraction_data.
        """
        if "set_params" not in result:
            return
        new_params = result.pop("set_params")
        if isinstance(new_params, dict):
            self.params_config.update(new_params)
            logger.info("tool.set_params", component="tool", keys=list(new_params.keys()))

    async def _execute_action(self, action_type: str, action_text: str) -> Dict[str, Any]:
        """Execute a specific browser action (legacy mode - no schema support)."""
        if action_type == "goto":
            return await self.stagehand_client.navigate_to(action_text)
        elif action_type == "act":
            return await self.stagehand_client.perform_action(action_text)
        elif action_type == "observe":
            return await self.stagehand_client.observe_elements(action_text)
        elif action_type == "extract":
            # Legacy mode: No schema available, return error for v3
            logger.error("tool.legacy_extract_no_schema", component="tool")
            return {
                "status": "error",
                "message": "Extract requires schema in Stagehand v3. Please update SOPs to YAML format with schema field.",
                "action_type": "extract",
            }
        else:
            logger.warning("tool.unknown_action_type", component="tool", action_type=action_type)
            return {"status": "error", "message": f"Unknown action type: {action_type}"}
