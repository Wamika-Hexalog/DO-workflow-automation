"""
LLM client integration for the Browser Auto agent.

This module contains the LLM client and related functionality for decision making.
"""

import json
import base64
from typing import Optional, Dict, Any
from dataclasses import dataclass

import structlog
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage
from langsmith import traceable
from ..utils import DecisionParser, ScreenshotUtils, AgentLogger

# Try to import Google GenAI for Computer Use support
try:
    from google import genai
    from google.genai import types

    HAS_GOOGLE_GENAI = True
except ImportError:
    HAS_GOOGLE_GENAI = False

logger = structlog.get_logger(__name__)


# ============================================================================
# Token Usage Tracking (for individual LLM call logging)
# ============================================================================


@dataclass
class TokenUsage:
    """Token usage information for a single LLM call."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""


class GeminiPricing:
    """Pricing calculator for Google Gemini models."""

    # Pricing per 1M tokens (as of Feb 2026)
    PRICING = {
        "gemini-2.5-flash": {
            "prompt": 0.075,  # $0.075 per 1M input tokens
            "completion": 0.30,  # $0.30 per 1M output tokens
        },
        "gemini-2.5-computer-use-preview-10-2025": {
            "prompt": 0.50,  # $0.50 per 1M input tokens
            "completion": 2.00,  # $2.00 per 1M output tokens
        },
    }

    @classmethod
    def create_usage(cls, model: str, prompt_tokens: int, completion_tokens: int) -> TokenUsage:
        """Create a TokenUsage object with cost calculation."""
        pricing = cls.PRICING.get(model, cls.PRICING["gemini-2.5-flash"])

        # Calculate cost (price is per 1M tokens)
        prompt_cost = (prompt_tokens / 1_000_000) * pricing["prompt"]
        completion_cost = (completion_tokens / 1_000_000) * pricing["completion"]
        total_cost = prompt_cost + completion_cost

        return TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            cost_usd=total_cost,
            model=model,
        )


class LLMClient:
    """Client for LLM interactions and decision making with dynamic model selection."""

    def __init__(
        self,
        model: str = "gemini-2.5-flash",
        temperature: float = 0.1,
        api_key: Optional[str] = None,
        enable_dynamic_switching: bool = True,
    ):
        """
        Initialize the LLM client.

        Args:
            model: The default LLM model to use (default: gemini-2.5-flash)
            temperature: Temperature for response generation
            api_key: Google API key (if None, will use environment variable)
            enable_dynamic_switching: Enable intelligent model switching based on task complexity

        Raises:
            ValueError: If api_key is not provided and GOOGLE_API_KEY env var is not set
        """
        # Validate API key early
        if api_key is None:
            import os

            api_key = os.getenv("GOOGLE_API_KEY")
            if not api_key:
                raise ValueError(
                    "GOOGLE_API_KEY is required. Either pass api_key parameter or set GOOGLE_API_KEY environment variable."
                )

        self.default_model = model
        self.flash_model = "gemini-2.5-flash"
        self.computer_use_model = "gemini-2.5-computer-use-preview-10-2025"
        self._temperature = temperature
        self.api_key = api_key
        self.enable_dynamic_switching = enable_dynamic_switching

        # Initialize Computer Use conversation history
        self._computer_use_history = []

        # Initialize both clients for dynamic switching
        if enable_dynamic_switching:
            # Initialize Flash model (LangChain)
            self.llm_flash = ChatGoogleGenerativeAI(
                model=self.flash_model, google_api_key=api_key, temperature=temperature
            )

            # Initialize Computer Use model (native SDK)
            if HAS_GOOGLE_GENAI:
                self.genai_client = genai.Client(api_key=api_key)
                logger.info("llm.model_init", component="llm", mode="dynamic_switching")
            else:
                logger.warning("llm.genai_unavailable", component="llm")
                self.genai_client = None

            self.llm = self.llm_flash
            self.model = self.default_model

        else:
            # Single model mode (legacy)
            self.model = model
            self.use_computer_use = "computer-use" in model.lower()

            if self.use_computer_use:
                if not HAS_GOOGLE_GENAI:
                    raise ImportError(
                        "google-genai package is required for Computer Use models. "
                        "Install it with: pip install google-genai"
                    )
                self.genai_client = genai.Client(api_key=api_key)
                self.llm = None
                self.llm_flash = None
                logger.info("llm.model_init", component="llm", mode="computer_use", model=model)
            else:
                self.llm = ChatGoogleGenerativeAI(
                    model=model, google_api_key=api_key, temperature=temperature
                )
                self.genai_client = None
                self.llm_flash = None
                logger.info("llm.model_init", component="llm", mode="standard", model=model)

    @traceable(name="llm_call")
    async def make_decision(
        self,
        prompt: str,
        screenshot_base64: Optional[str] = None,
        use_computer_use: bool = False,
        model_reason: str = "",
        extraction_mode: bool = False,
    ) -> Dict[str, Any]:
        """
        Make a decision using the LLM with optional visual context.

        Args:
            prompt: The text prompt for the LLM
            screenshot_base64: Optional base64 encoded screenshot for visual context
            use_computer_use: Force use of Computer Use model (overrides dynamic switching)
            model_reason: Reason for model selection
            extraction_mode: If True, use Computer Use in extraction/analysis mode (no browser tools)

        Returns:
            Dictionary containing the LLM response and parsed decision
        """
        try:
            # Determine which model to use
            if self.enable_dynamic_switching:
                # Dynamic switching mode - use parameter to decide
                should_use_cu = use_computer_use and self.genai_client is not None

                # Warn if Computer Use was requested but unavailable
                if use_computer_use and self.genai_client is None:
                    logger.warning(
                        "llm.computer_use_unavailable",
                        component="llm",
                        fallback="flash",
                    )
            else:
                # Legacy mode - use configured model
                should_use_cu = getattr(self, "use_computer_use", False)

            # Make decision with selected model
            if should_use_cu:
                result = await self._make_computer_use_decision(
                    prompt, screenshot_base64, extraction_mode=extraction_mode
                )
                result["model_reason"] = model_reason
                return result
            else:
                result = await self._make_standard_decision(prompt, screenshot_base64)
                result["model_reason"] = model_reason
                return result

        except Exception as e:
            logger.error("llm.decision_error", component="llm", error=str(e), handled=False)
            return {
                "status": "error",
                "error": str(e),
                "parsed_decision": {
                    "action_type": "end",
                    "description": "Error in LLM decision making",
                    "reasoning": f"Error: {str(e)}",
                    "action_text": "",
                },
            }

    async def _make_standard_decision(
        self, prompt: str, screenshot_base64: Optional[str] = None
    ) -> Dict[str, Any]:
        """Make decision using standard LangChain model."""
        # Create messages for the LLM with optional screenshot
        if screenshot_base64:
            # Create multimodal message with text + image
            llm_messages = [
                HumanMessage(
                    content=[
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{screenshot_base64}"},
                        },
                    ]
                )
            ]
        else:
            # Text-only message
            llm_messages = [HumanMessage(content=prompt)]

        # Get response from LLM (use llm_flash if in dynamic mode)
        llm_to_use = self.llm_flash if self.enable_dynamic_switching else self.llm
        response = await llm_to_use.ainvoke(llm_messages)
        decision_text = response.content

        # Detect actual model used
        if self.enable_dynamic_switching:
            actual_model = self.flash_model
            model_label = "Flash"
            logger.info(
                "llm.response_received",
                component="llm",
                model_label=model_label,
                actual_model=actual_model,
            )
        else:
            actual_model = getattr(llm_to_use, "model", self.model)
            model_label = actual_model
            logger.info("llm.response_received", component="llm", model_label=model_label)

        logger.debug("llm.response", component="llm", decision_text=decision_text)

        # Extract token usage from response
        token_usage = None
        if hasattr(response, "response_metadata") and response.response_metadata:
            usage_metadata = response.response_metadata.get("usage_metadata", {})
            if usage_metadata:
                prompt_tokens = usage_metadata.get("prompt_token_count", 0)
                completion_tokens = usage_metadata.get("candidates_token_count", 0)

                # Only create token usage if we have non-zero values
                if prompt_tokens > 0 or completion_tokens > 0:
                    model_name = actual_model
                    token_usage = GeminiPricing.create_usage(
                        model_name, prompt_tokens, completion_tokens
                    )
                    logger.debug(
                        "llm.token_usage",
                        component="llm",
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                    )
                else:
                    logger.warning("llm.token_counts_zero", component="llm")
            else:
                logger.warning("llm.no_usage_metadata", component="llm")
                logger.debug(
                    "llm.response_metadata_keys",
                    component="llm",
                    metadata_keys=list(response.response_metadata.keys()),
                )
        else:
            logger.warning("llm.no_response_metadata", component="llm")

        # If token usage is still None, try to estimate from text length
        if token_usage is None:
            logger.info("llm.tokens_estimating", component="llm")
            # Rough estimate: ~4 characters per token
            prompt_est = len(prompt) // 4
            completion_est = len(decision_text) // 4
            model_name = actual_model
            token_usage = GeminiPricing.create_usage(model_name, prompt_est, completion_est)
            logger.info(
                "llm.tokens_estimated",
                component="llm",
                prompt_tokens=prompt_est,
                completion_tokens=completion_est,
            )

        # Parse the JSON response using utility
        decision_parser = DecisionParser()
        try:
            decision = decision_parser.parse_decision(decision_text)
        except Exception as parse_error:
            logger.error("llm.parse_error", component="llm", error=str(parse_error))
            logger.error("llm.parse_error_raw", component="llm", decision_text=decision_text)
            raise

        return {
            "status": "success",
            "raw_response": decision_text,
            "parsed_decision": decision,
            "has_screenshot": screenshot_base64 is not None,
            "model_used": model_label.lower(),
            "token_usage": token_usage,
        }

    async def _make_computer_use_decision(
        self, prompt: str, screenshot_base64: Optional[str] = None, extraction_mode: bool = False
    ) -> Dict[str, Any]:
        """Make decision using Computer Use model with native SDK following official Google patterns."""
        if extraction_mode:
            logger.info("llm.computer_use_start", component="llm", mode="extraction")
        else:
            logger.info("llm.computer_use_start", component="llm", mode="browser")

        # Prepare content parts
        content_parts = [types.Part(text=prompt)]

        # Add screenshot if available (required for Computer Use)
        if screenshot_base64:
            screenshot_bytes = base64.b64decode(screenshot_base64)
            content_parts.append(
                types.Part.from_bytes(data=screenshot_bytes, mime_type="image/png")
            )
            logger.info("llm.screenshot_attached", component="llm")
        else:
            logger.warning("llm.screenshot_missing", component="llm")

        # Configure with Computer Use tool (required by this model)
        config = types.GenerateContentConfig(
            tools=[
                types.Tool(
                    computer_use=types.ComputerUse(
                        environment=types.Environment.ENVIRONMENT_BROWSER
                    )
                )
            ],
            temperature=self._temperature,
        )

        if extraction_mode:
            logger.info("llm.computer_use_configured", component="llm", mode="extraction")
        else:
            logger.info("llm.computer_use_configured", component="llm", mode="browser")

        # Build conversation with history
        contents = self._computer_use_history + [types.Content(role="user", parts=content_parts)]

        # Generate content
        response = self.genai_client.models.generate_content(
            model=self.computer_use_model,
            contents=contents,
            config=config,
        )

        # Update conversation history for next turn
        if response.candidates and len(response.candidates) > 0:
            self._computer_use_history.append(types.Content(role="user", parts=content_parts))
            self._computer_use_history.append(response.candidates[0].content)

        # Extract response and handle function calls (following official docs pattern)
        decision_text = ""
        function_calls = []
        safety_decision = None

        if response.candidates and len(response.candidates) > 0:
            candidate = response.candidates[0]

            # Check for safety_decision (official Computer Use feature)
            if hasattr(candidate, "safety_decision") and candidate.safety_decision:
                safety_decision = candidate.safety_decision
                logger.info("llm.safety_decision", component="llm", safety_decision=safety_decision)

            for part in candidate.content.parts:
                # Check for text response
                if hasattr(part, "text") and part.text:
                    decision_text += part.text

                # Check for function call (Computer Use action)
                if hasattr(part, "function_call") and part.function_call:
                    function_calls.append(part.function_call)

        # Handle Computer Use function calls (official pattern)
        if function_calls:
            fc = function_calls[0]
            AgentLogger.function_call(fc.name, dict(fc.args) if fc.args else {})

            # In extraction_mode, function calls are unexpected - log warning
            if extraction_mode:
                logger.warning(
                    "llm.extraction_unexpected_function_call",
                    component="llm",
                    function_name=fc.name,
                )
                logger.warning("llm.extraction_function_call_tip", component="llm")
                # Return error for extraction mode function calls
                decision = {
                    "extraction_error": True,
                    "message": f"Model returned function call '{fc.name}' instead of data",
                    "function_call": fc.name,
                }
            else:
                # Check if action requires user confirmation (safety feature)
                requires_confirmation = (
                    safety_decision
                    and hasattr(safety_decision, "require_confirmation")
                    and safety_decision.require_confirmation
                )

                if requires_confirmation:
                    logger.warning("llm.requires_confirmation", component="llm")
                    decision = {
                        "action_type": "end",
                        "description": f"Action '{fc.name}' requires user confirmation",
                        "reasoning": f"Safety system flagged this action as requiring confirmation: {fc.name}",
                        "action_text": "",
                        "requires_confirmation": True,
                        "function_call": fc,
                    }
                else:
                    # Convert function call to our action format
                    decision = self._convert_function_call_to_decision(fc)
                    logger.info(
                        "llm.action_converted",
                        component="llm",
                        action_type=decision["action_type"],
                    )

        elif decision_text:
            # Text response - expected in extraction_mode
            if extraction_mode:
                logger.info(
                    "llm.extraction_text_response",
                    component="llm",
                    char_count=len(decision_text),
                )
                # Return raw text for extraction parsing
                decision = {
                    "status": "success",
                    "raw_response": decision_text,
                    "extraction_text": decision_text,
                }
            else:
                logger.info(
                    "llm.text_response_unexpected",
                    component="llm",
                    decision_text=decision_text,
                )
                decision_parser = DecisionParser()
                decision = decision_parser.parse_decision(decision_text)
        else:
            # No response - task might be complete
            logger.warning("llm.no_response", component="llm")
            decision = {
                "action_type": "end",
                "description": "Computer Use model provided no action",
                "reasoning": "The model has completed the task or reached a stopping point",
                "action_text": "",
            }

        # Extract token usage from response
        token_usage = None
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            prompt_tokens = response.usage_metadata.prompt_token_count or 0
            completion_tokens = response.usage_metadata.candidates_token_count or 0

            # Only create token usage if we have non-zero values
            if prompt_tokens > 0 or completion_tokens > 0:
                model_name = self.computer_use_model
                token_usage = GeminiPricing.create_usage(
                    model_name, prompt_tokens, completion_tokens
                )
                logger.debug(
                    "llm.computer_use_token_usage",
                    component="llm",
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
            else:
                logger.warning("llm.computer_use_token_counts_zero", component="llm")
        else:
            logger.warning("llm.computer_use_no_usage_metadata", component="llm")

        # If token usage is still None, estimate from text length
        if token_usage is None:
            logger.info("llm.computer_use_tokens_estimating", component="llm")
            prompt_est = len(prompt) // 4
            completion_est = len(decision_text or "") // 4
            model_name = self.computer_use_model
            token_usage = GeminiPricing.create_usage(model_name, prompt_est, completion_est)
            logger.info(
                "llm.computer_use_tokens_estimated",
                component="llm",
                prompt_tokens=prompt_est,
                completion_tokens=completion_est,
            )

        # Safe raw_response construction
        if decision_text:
            raw_response = decision_text
        elif function_calls and len(function_calls) > 0:
            raw_response = f"Function call: {function_calls[0].name}"
        else:
            raw_response = "No response"

        # Build response dict
        result = {
            "status": "success",
            "raw_response": raw_response,
            "has_screenshot": screenshot_base64 is not None,
            "computer_use_active": not extraction_mode,
            "model_used": "computer-use",
            "token_usage": token_usage,
        }

        # In extraction_mode, return extraction_text for JSON parsing
        if extraction_mode:
            result["extraction_text"] = decision_text if decision_text else raw_response
        else:
            result["parsed_decision"] = decision

        return result

    def _convert_function_call_to_decision(self, function_call) -> Dict[str, Any]:
        """
        Convert Computer Use function call to standard decision format.

        Based on official Computer Use actions from:
        https://ai.google.dev/gemini-api/docs/computer-use#supported-actions

        Args:
            function_call: The function call from Computer Use model

        Returns:
            Decision dictionary compatible with the agent

        Raises:
            ValueError: If function_call is None or missing required attributes
        """
        # Validate input
        if function_call is None:
            raise ValueError("function_call cannot be None")
        if not hasattr(function_call, "name"):
            raise ValueError(f"function_call missing 'name' attribute: {function_call}")

        function_name = function_call.name
        args = function_call.args or {}

        logger.info(
            "llm.action_converting",
            component="llm",
            function_name=function_name,
            args=args,
        )

        # Official Computer Use actions (per Google docs)

        # 1. click(x, y) - Click at specific coordinates
        if function_name == "click":
            x = args.get("x", 0)
            y = args.get("y", 0)
            return {
                "action_type": "act",
                "description": f"Click at coordinates ({x}, {y})",
                "reasoning": f"Computer Use model detected clickable element at ({x}, {y})",
                "action_text": f"Click at coordinates {x}, {y}",
            }

        # 2. type(text) - Type text
        elif function_name == "type":
            text = args.get("text", "")
            return {
                "action_type": "act",
                "description": f"Type text: {text}",
                "reasoning": f"Computer Use model wants to type: {text}",
                "action_text": f"Type {text}",
            }

        # 3. key(key_name) - Press keyboard key
        elif function_name == "key":
            key_name = args.get("key_name", "")
            return {
                "action_type": "act",
                "description": f"Press key: {key_name}",
                "reasoning": f"Computer Use model wants to press {key_name} key",
                "action_text": f"Press {key_name} key",
            }

        # 4. scroll(direction, amount) - Scroll the page
        elif function_name == "scroll":
            direction = args.get("direction", "down")
            amount = args.get("amount", 1)
            return {
                "action_type": "act",
                "description": f"Scroll {direction} by {amount}",
                "reasoning": f"Computer Use model wants to scroll {direction}",
                "action_text": f"Scroll {direction} {amount}",
            }

        # 5. navigate(url) - Navigate to URL
        elif function_name in ["navigate", "goto"]:
            url = args.get("url", "")
            return {
                "action_type": "goto",
                "description": f"Navigate to {url}",
                "reasoning": f"Computer Use model wants to navigate to {url}",
                "action_text": url,
            }

        # 6. hover(x, y) - Hover over coordinates
        elif function_name == "hover":
            x = args.get("x", 0)
            y = args.get("y", 0)
            return {
                "action_type": "act",
                "description": f"Hover at ({x}, {y})",
                "reasoning": f"Computer Use model wants to hover at ({x}, {y})",
                "action_text": f"Hover at {x}, {y}",
            }

        # 7. done() - Task completion
        elif function_name == "done":
            message = args.get("message", "Task completed")
            return {
                "action_type": "end",
                "description": "Task completed",
                "reasoning": message,
                "action_text": "",
            }

        # 8. Additional Computer Use actions (observed in practice)
        elif function_name == "open_web_browser":
            url = args.get("url", "")
            return {
                "action_type": "goto",
                "description": f"Open web browser to {url}" if url else "Open web browser",
                "reasoning": f"Computer Use wants to open browser{' to ' + url if url else ''}",
                "action_text": url if url else "about:blank",
            }

        elif function_name in ["type_text_at", "type_at"]:
            x = args.get("x", 0)
            y = args.get("y", 0)
            text = args.get("text", "")
            return {
                "action_type": "act",
                "description": f"Type '{text}' at ({x}, {y})",
                "reasoning": f"Computer Use wants to type at coordinates ({x}, {y})",
                "action_text": f"Click at coordinates {x}, {y} and type {text}",
            }

        elif function_name == "click_at":
            x = args.get("x", 0)
            y = args.get("y", 0)
            return {
                "action_type": "act",
                "description": f"Click at coordinates ({x}, {y})",
                "reasoning": f"Computer Use wants to click at ({x}, {y})",
                "action_text": f"Click at coordinates {x}, {y}",
            }

        # 9. Fallback for unknown actions
        else:
            logger.warning("llm.unknown_function", component="llm", function_name=function_name)
            # Try to convert to a generic action
            return {
                "action_type": "act",
                "description": f"Execute: {function_name}",
                "reasoning": f"Computer Use suggested action: {function_name} with {args}",
                "action_text": f"{function_name} {' '.join(str(v) for v in args.values())}",
            }

    def reset_computer_use_history(self):
        """Reset Computer Use conversation history for new task."""
        if hasattr(self, "_computer_use_history"):
            self._computer_use_history = []
            logger.info("llm.computer_use_history_reset", component="llm")

    def encode_screenshot(self, screenshot_bytes: bytes) -> str:
        """
        Encode screenshot bytes to base64 string.

        Args:
            screenshot_bytes: Raw screenshot bytes

        Returns:
            Base64 encoded string
        """
        return ScreenshotUtils.encode_to_base64(screenshot_bytes)

    def build_decision_prompt(
        self,
        sop: str,
        iteration_count: int,
        max_iterations: int,
        current_action: Optional[str],
        action_result: Optional[Dict[str, Any]],
    ) -> str:
        """
        Build the decision prompt for the LLM.

        Args:
            sop: Standard Operating Procedure
            iteration_count: Current iteration number
            max_iterations: Maximum number of iterations
            current_action: Previous action taken
            action_result: Result of previous action

        Returns:
            Formatted prompt string
        """
        # Simplified prompt for Computer Use model
        return f"""You are controlling a web browser to complete this task:

TASK: {sop}

PROGRESS: Step {iteration_count + 1}/{max_iterations}
LAST ACTION: {current_action if current_action else "Starting"}
RESULT: {json.dumps(action_result) if action_result else "None"}

Analyze the screenshot and take the next action. Use browser functions to navigate, click, type, or extract data. When complete, use 'done' with all extracted information.

For Flash model (if JSON needed):
{{
    "reasoning": "Brief reason (include extracted data if ending)",
    "action_type": "goto|act|observe|extract|end",
    "description": "What to do",
    "action_text": "URL or specific action instruction"
}}"""

    # ========================================================================
    # Structured Output Methods (Phase 3)
    # ========================================================================

    async def make_structured_decision(
        self, prompt: str, screenshot_base64: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Make decision using Gemini structured output with Pydantic schema.

        This uses the Flash model with JSON schema validation to ensure
        consistent, type-safe responses with confidence and risk scoring.

        Args:
            prompt: The prompt for decision-making
            screenshot_base64: Optional screenshot for visual context

        Returns:
            Dict with structured decision data
        """
        import time
        from .llm_models import LLMDecision

        start_time = time.time()

        try:
            # Build message with optional screenshot
            content_parts = [{"type": "text", "text": prompt}]

            if screenshot_base64:
                content_parts.append(
                    {"type": "image_url", "image_url": f"data:image/png;base64,{screenshot_base64}"}
                )

            message = HumanMessage(content=content_parts)

            # Configure structured output with Pydantic schema
            config = {
                "response_mime_type": "application/json",
                "response_json_schema": LLMDecision.model_json_schema(),
            }

            # Invoke Flash model with schema
            response = await self.llm_flash.ainvoke([message], config=config)

            # Strip markdown code blocks if present (```json ... ```)
            content = response.content.strip()
            if content.startswith("```"):
                # Remove code block markers
                lines = content.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]  # Remove opening ```json
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]  # Remove closing ```
                content = "\n".join(lines)

            # Parse with Pydantic
            decision = LLMDecision.model_validate_json(content)

            # Extract token usage
            tokens = self._extract_token_usage(response)

            duration_ms = int((time.time() - start_time) * 1000)

            logger.info(
                "llm.structured_decision",
                component="llm",
                chosen_action=decision.chosen_action,
                confidence=decision.confidence,
                risk_level=decision.risk_level,
            )

            return {
                "decision": decision.model_dump(),
                "raw_response": response.content,
                "model": self.flash_model,
                "tokens": tokens,
                "duration_ms": duration_ms,
                "structured": True,
            }

        except Exception as e:
            logger.error("llm.structured_decision_error", component="llm", error=str(e))
            raise

    async def make_popup_analysis(self, screenshot_base64: str, prompt: str) -> Dict[str, Any]:
        """
        Analyze popup with structured output for self-healing.

        Uses Gemini Flash with Pydantic schema to ensure consistent
        popup detection with confidence and risk scoring.

        Args:
            screenshot_base64: Screenshot for popup analysis
            prompt: Prompt for popup detection

        Returns:
            Dict with structured popup analysis
        """
        import time
        from .llm_models import PopupAnalysis

        start_time = time.time()

        try:
            message = HumanMessage(
                content=[
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": f"data:image/png;base64,{screenshot_base64}",
                    },
                ]
            )

            # Configure structured output
            config = {
                "response_mime_type": "application/json",
                "response_json_schema": PopupAnalysis.model_json_schema(),
            }

            response = await self.llm_flash.ainvoke([message], config=config)

            # Strip markdown code blocks if present (```json ... ```)
            content = response.content.strip()
            if content.startswith("```"):
                # Remove code block markers
                lines = content.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]  # Remove opening ```json
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]  # Remove closing ```
                content = "\n".join(lines)

            analysis = PopupAnalysis.model_validate_json(content)

            duration_ms = int((time.time() - start_time) * 1000)
            tokens = self._extract_token_usage(response)

            return {
                "analysis": analysis.model_dump(),
                "raw_response": response.content,
                "duration_ms": duration_ms,
                "tokens": tokens,
                "model": self.flash_model,
            }

        except Exception as e:
            logger.error("llm.popup_analysis_error", component="llm", error=str(e))
            raise

    def _extract_token_usage(self, response: Any) -> Dict[str, int]:
        """
        Extract token usage from LLM response.

        Args:
            response: LLM response object

        Returns:
            Dict with 'input' and 'output' token counts
        """
        tokens = {"input": 0, "output": 0}

        try:
            # Try to extract from response_metadata (LangChain format)
            if hasattr(response, "response_metadata"):
                metadata = response.response_metadata
                if "usage_metadata" in metadata:
                    usage = metadata["usage_metadata"]
                    tokens["input"] = usage.get("input_tokens", 0)
                    tokens["output"] = usage.get("output_tokens", 0)
                elif "token_usage" in metadata:
                    usage = metadata["token_usage"]
                    tokens["input"] = usage.get("prompt_tokens", 0)
                    tokens["output"] = usage.get("completion_tokens", 0)

            # Alternative: try direct attributes
            elif hasattr(response, "usage_metadata"):
                usage = response.usage_metadata
                tokens["input"] = getattr(usage, "input_tokens", 0)
                tokens["output"] = getattr(usage, "output_tokens", 0)

        except Exception as e:
            logger.debug("llm.token_extract_failed", component="llm", error=str(e))

        return tokens

    @property
    def model_name(self) -> str:
        """Get the model name."""
        return self.model

    @property
    def temperature(self) -> float:
        """Get the temperature setting."""
        return self._temperature
