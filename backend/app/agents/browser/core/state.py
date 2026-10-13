"""
State management for the Browser Auto agent.

This module contains the AgentState definition and related data models.
"""

from typing import Optional, Dict, Any, List, TypedDict, Annotated
from uuid import UUID


class AgentState(TypedDict):
    """
    State for the LangGraph agent workflow.

    This TypedDict defines all fields required for agent execution. All fields
    must be present when creating an AgentState instance (though some can be None).
    """

    messages: Annotated[List[Dict[str, Any]], "List of messages in the conversation"]
    sop: str  # Standard Operating Procedure
    current_action: Optional[str]  # Current action to be executed
    action_result: Optional[Dict[str, Any]]  # Result of the last action
    stagehand_inputs: Optional[Dict[str, Any]]  # Inputs for Stagehand
    iteration_count: int  # Track iterations to prevent infinite loops
    max_iterations: int  # Maximum number of iterations
    current_step_idx: int  # Current SOP step index (0-based)
    consecutive_failures: int  # Count of consecutive failures on current step

    # Database tracking
    run_id: Optional[UUID]  # Database run ID for this automation execution

    # Self-healing fields
    self_healing_enabled: bool  # Whether self-healing is enabled
    popups_closed: int  # Total number of popups closed in this session
    healing_attempts_current_page: int  # Healing attempts for current page
    last_healing_result: Optional[Dict[str, Any]]  # Result of last healing attempt

    # The window/context the agent is currently working in (e.g. "job_popup").
    # Updated by the tool node so self-healing screenshots the active window
    # instead of the frozen first page. None = main page.
    active_context: Optional[str]

    # Graceful failure fields — set by decision_node instead of raising an exception,
    # so ainvoke() returns normally and extraction_data is preserved in execute_sop.
    stop_execution: bool  # Signal _should_continue to route to END immediately
    sop_failed: bool  # True when the SOP terminated due to a step failure
    sop_failure_reason: Optional[str]  # Human-readable reason for the failure
