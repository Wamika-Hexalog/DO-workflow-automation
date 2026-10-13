"""
Pydantic models for structured LLM outputs.

These models define the schema for:
1. Agent decision-making with confidence and risk scoring
2. Self-healing popup analysis with detection strategy
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Literal


class Alternative(BaseModel):
    """Alternative action that was considered but not chosen."""

    action: str = Field(description="The alternative action that was considered")

    reason_not_chosen: str = Field(
        description="Brief explanation of why this alternative wasn't selected"
    )


class LLMDecision(BaseModel):
    """
    Structured output for agent decision-making.

    This model enforces a consistent schema for LLM decisions, ensuring:
    - Clear reasoning for every decision
    - Confidence scoring (0.0-1.0)
    - Risk assessment (low/medium/high)
    - Visibility into alternatives considered

    Used by: DecisionNode when agent decides next action
    """

    chosen_action: str = Field(
        description="The action the agent decided to take (e.g., 'navigate_to', 'click_button', 'extract_data')"
    )

    reasoning: str = Field(
        description="Step-by-step reasoning for this decision (2-3 sentences explaining why this action was chosen)"
    )

    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Confidence level in this decision:\n"
            "- 0.9-1.0: Very confident, action is clear and straightforward\n"
            "- 0.7-0.9: Reasonably confident, action should work\n"
            "- 0.5-0.7: Moderate confidence, some uncertainty\n"
            "- 0.0-0.5: Low confidence, high risk of failure"
        ),
    )

    risk_level: Literal["low", "medium", "high"] = Field(
        description=(
            "Risk assessment for this action:\n"
            "- LOW: Safe action, well-understood, unlikely to fail\n"
            "- MEDIUM: Some uncertainty, may need retry or adjustment\n"
            "- HIGH: Uncertain action, might fail or cause issues"
        )
    )

    alternatives: Optional[List[Alternative]] = Field(
        default=None,
        max_length=2,
        description="Other actions that were considered (maximum 2 alternatives)",
    )


class PopupAnalysis(BaseModel):
    """
    Structured output for self-healing popup detection.

    This model provides a consistent schema for popup analysis, enabling:
    - Reliable popup detection with confidence scoring
    - Risk-aware closing strategies
    - Transparent reasoning for debugging

    Used by: SelfHealingNode when detecting and closing popups
    """

    has_popup: bool = Field(
        description="Whether a popup, modal, overlay, or banner is detected on the page"
    )

    popup_type: Literal["ad", "cookie", "newsletter", "modal", "overlay", "none"] = Field(
        description=(
            "Type of popup detected:\n"
            "- ad: Advertisement or promotional popup\n"
            "- cookie: Cookie consent banner\n"
            "- newsletter: Email signup or newsletter prompt\n"
            "- modal: Modal dialog (account, verification, etc.)\n"
            "- overlay: Semi-transparent overlay\n"
            "- none: No popup detected"
        )
    )

    blocking_content: bool = Field(
        description="Whether the popup is blocking access to main content (true = needs closing)"
    )

    close_method: Literal["click_button", "click_outside", "press_escape", "none"] = Field(
        description=(
            "Recommended method to close the popup:\n"
            "- click_button: Click a close button (X, 'Accept', 'Decline', etc.)\n"
            "- click_outside: Click outside the popup area\n"
            "- press_escape: Press Escape key\n"
            "- none: No action needed (not blocking or no popup)"
        )
    )

    close_selector: Optional[str] = Field(
        default=None,
        description="CSS selector for close button (e.g., '.cookie-banner .accept', '[data-dismiss]')",
    )

    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Confidence in popup detection:\n"
            "- 0.9-1.0: Clear popup with obvious close button\n"
            "- 0.7-0.9: Likely popup, close mechanism identified\n"
            "- 0.5-0.7: Uncertain if popup, or close method unclear\n"
            "- 0.0-0.5: Very uncertain, may not be a popup"
        ),
    )

    risk_level: Literal["low", "medium", "high"] = Field(
        description=(
            "Risk of closing this popup:\n"
            "- LOW: Safe to close, clear close button, won't break page\n"
            "- MEDIUM: Some risk, may need multiple attempts\n"
            "- HIGH: High risk, might accidentally navigate away or skip required step"
        )
    )

    reasoning: str = Field(
        description=(
            "Brief explanation (2-3 sentences) of:\n"
            "1. Why this is/isn't a popup\n"
            "2. The closing strategy\n"
            "3. Any risks or concerns"
        )
    )


# Export all models
__all__ = [
    "LLMDecision",
    "PopupAnalysis",
    "Alternative",
]
