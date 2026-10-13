"""
Task Complexity Analyzer for intelligent model selection.

This module analyzes the current task context to determine whether to use
a lightweight model (Flash) or the powerful Computer Use model.
"""

from typing import Dict, Any, Optional, List

import structlog

logger = structlog.get_logger(__name__)


class TaskComplexityAnalyzer:
    """Analyzes task complexity to determine appropriate model selection."""

    # Keywords indicating complex tasks requiring Computer Use
    COMPLEX_KEYWORDS = [
        "extract",
        "find",
        "search for",
        "get",
        "retrieve",
        "analyze",
        "compare",
        "identify",
        "locate",
        "read",
        "scrape",
        "collect",
        "gather",
        "parse",
        "detect",
        "discover",
        "observe",
        "data",
        "information",
        "details",
        "prices",
        "titles",
        "products",
    ]

    # Keywords indicating tasks that need element finding (visual context)
    # These require Computer Use even though the action seems simple
    VISUAL_ELEMENT_KEYWORDS = [
        "search box",
        "search bar",
        "input",
        "field",
        "textbox",
        "text box",
        "button",
        "link",
        "menu",
        "dropdown",
        "checkbox",
        "radio",
        "in the",
        "into the",
        "on the",  # Indicators of targeting specific elements
    ]

    # Keywords indicating simple tasks suitable for Flash (URL navigation only)
    SIMPLE_KEYWORDS = ["navigate", "goto", "open url", "visit"]

    def __init__(self):
        """Initialize the complexity analyzer."""
        self.logger = structlog.get_logger(__name__)
        self.yaml_steps: Optional[List[Dict[str, Any]]] = None
        self.last_model_used: Optional[str] = None
        self.last_step_idx: Optional[int] = None
        self.step_stuck_count: int = 0  # How many iterations on same step

    def set_yaml_steps(self, steps: List[Dict[str, Any]]):
        """Set structured YAML steps for analysis."""
        self.yaml_steps = steps

    def analyze_task_complexity(
        self,
        sop: str,
        current_step_idx: int,
        consecutive_failures: int,
        current_action: Optional[str],
        action_result: Optional[Dict[str, Any]],
    ) -> tuple[str, str]:
        """
        Analyze task complexity and return recommended model.

        Args:
            sop: The Standard Operating Procedure
            current_step_idx: Current SOP step index from state
            consecutive_failures: Number of consecutive failures on current step
            current_action: Previous action description
            action_result: Result of previous action

        Returns:
            Tuple of (model_name, reason)
        """
        from .logger import AgentLogger

        # Use YAML steps
        if self.yaml_steps:
            steps = self.yaml_steps
            if current_step_idx == 0 and consecutive_failures == 0:
                AgentLogger.info(f"✓ Using YAML format with {len(steps)} steps")
        else:
            steps = []
            if current_step_idx == 0 and consecutive_failures == 0:
                AgentLogger.info("⚠️  No YAML steps available - analyzing entire SOP")

        # Ensure step index is within bounds
        if steps:
            current_step_idx = min(current_step_idx, len(steps) - 1)

        complexity_score = 0
        reasons = []

        # Check for failures and escalate if needed
        if consecutive_failures >= 2:
            # After 2+ failures, escalate to Computer Use
            complexity_score += 15
            if self.last_model_used == "flash":
                reasons.append(
                    f"Flash failed {consecutive_failures} times - escalating to Computer Use"
                )
                AgentLogger.retry(
                    f"Flash failed on step {current_step_idx + 1} (attempt {consecutive_failures + 1})"
                )
            else:
                reasons.append(f"Multiple failures on step {current_step_idx + 1} - retrying")
                AgentLogger.retry(
                    f"Retrying step {current_step_idx + 1} (attempt {consecutive_failures + 1})"
                )
        elif consecutive_failures == 1:
            # First failure - give a small boost to consider Computer Use
            complexity_score += 5
            reasons.append("Previous attempt failed - considering more powerful model")

        # Analyze current step based on YAML data
        if steps:
            # YAML format: use explicit action type
            step_data = steps[current_step_idx]
            action_type = step_data.get("action", "").lower()
            description = step_data.get("description", "").lower()

            if action_type == "observe":
                # Observe always needs Computer Use for visual analysis
                complexity_score += 10
                reasons.append("Observe action (complex)")
            elif action_type == "extract":
                # Extract uses Flash with screenshots (NOT Computer Use)
                # Computer Use tries to interact, Flash extracts from screenshot
                complexity_score -= 5
                reasons.append("Extract action (Flash with screenshot)")
            elif action_type == "goto":
                # Pure URL navigation is simple
                complexity_score -= 5
                reasons.append("URL navigation (simple)")
            elif action_type == "act":
                # Act actions use Stagehand's act primitive which handles element finding
                # Use Flash by default and let Stagehand handle the natural language
                # Only escalate to Computer Use if there are data extraction keywords
                has_extraction = any(
                    kw in description
                    for kw in ["extract", "find", "get", "retrieve", "scrape", "collect"]
                )
                if has_extraction:
                    complexity_score += 8
                    reasons.append("Act with data extraction")
                else:
                    complexity_score -= 3
                    reasons.append("Natural language action (Stagehand handles it)")
            elif action_type == "end":
                complexity_score -= 3
                reasons.append("End action (simple)")

            # Also check description for data extraction keywords
            complex_in_desc = [kw for kw in self.COMPLEX_KEYWORDS if kw in description]
            if complex_in_desc:
                complexity_score += len(complex_in_desc) * 2
        else:
            # No YAML steps - default to complex to be safe
            complexity_score += 5
            reasons.append("No structured steps - using cautious approach")

        # Check previous action context
        if current_action:
            action_lower = current_action.lower()

            # If previous action was complex, likely continuing complex task
            if any(kw in action_lower for kw in self.COMPLEX_KEYWORDS):
                complexity_score += 2
                reasons.append("Continuing complex task")

        # Check if action explicitly failed - escalate immediately
        if action_result and action_result.get("status") == "error":
            complexity_score += 10
            reasons.append("Previous action failed - retrying with Computer Use")
            AgentLogger.info("⚠️  Action error detected - escalating to Computer Use")

        # Decision logic (threshold = 3)
        if complexity_score >= 3:
            model = "computer-use"
            reason = reasons[0] if reasons else "Complex task"
        else:
            model = "flash"
            reason = reasons[0] if reasons else "Simple task"

        # Track the model for next iteration's fallback detection
        self.last_model_used = model

        return (model, reason)

    def should_use_computer_use(
        self,
        sop: str,
        current_step_idx: int = 0,
        consecutive_failures: int = 0,
        current_action: Optional[str] = None,
        action_result: Optional[Dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        """
        Determine if Computer Use model should be used.

        Returns:
            Tuple of (should_use_computer_use, reason)
        """
        model, reason = self.analyze_task_complexity(
            sop, current_step_idx, consecutive_failures, current_action, action_result
        )
        return (model == "computer-use", reason)

    def _parse_sop_steps(self, sop: str) -> List[str]:
        """
        Parse plain-text SOP string into a list of step descriptions.

        Expected SOP format (from SOPReader):
            1. [GOTO] Navigate to website
            2. [ACT] Click on button
            3. [EXTRACT] Get data

        Args:
            sop: SOP string to parse

        Returns:
            List of step description strings (without the numbering)
            Returns empty list if SOP is empty or malformed
        """
        if not sop or not sop.strip():
            self.logger.warning("complexity.empty_sop", component="complexity")
            return []

        steps = []
        lines = sop.strip().split("\n")

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Try to parse numbered step format: "1. [ACTION] Description"
            # or plain step format: "[ACTION] Description"
            try:
                # Check if line starts with a number followed by period
                if line[0].isdigit() and "." in line:
                    # Find the period and extract everything after it
                    period_idx = line.index(".")
                    step_desc = line[period_idx + 1 :].strip()
                    if step_desc:
                        steps.append(step_desc)
                elif line.startswith("["):
                    # Plain format without numbering
                    steps.append(line)
                else:
                    # Fallback: treat entire line as step
                    steps.append(line)
            except (IndexError, ValueError) as e:
                self.logger.debug(
                    "complexity.parse_line_failed",
                    component="complexity",
                    line=line,
                    error=str(e),
                )
                # Still add it as-is
                if line:
                    steps.append(line)

        if not steps:
            self.logger.warning("complexity.no_valid_steps", component="complexity")
            return []

        self.logger.debug(
            "complexity.parsed_steps",
            component="complexity",
            step_count=len(steps),
        )
        return steps
