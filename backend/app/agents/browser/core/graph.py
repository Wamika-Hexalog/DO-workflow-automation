"""
Graph construction for the Browser Auto agent.

This module contains the logic for building the LangGraph workflow.
"""

from langgraph.graph import StateGraph, END
from .state import AgentState


class GraphBuilder:
    """Builder class for constructing the agent workflow graph."""

    def __init__(self, decision_node, tool_node, self_healing_node, should_continue_func):
        """
        Initialize the graph builder.

        Args:
            decision_node: The decision node function
            tool_node: The tool node function
            self_healing_node: The self-healing node function
            should_continue_func: The continuation logic function
        """
        self.decision_node = decision_node
        self.tool_node = tool_node
        self.self_healing_node = self_healing_node
        self.should_continue_func = should_continue_func

    def build_graph(self) -> StateGraph:
        """Build the LangGraph with decision, tool, and self-healing nodes."""

        # Create the state graph
        workflow = StateGraph(AgentState)

        # Add nodes
        workflow.add_node("decision", self.decision_node)
        workflow.add_node("tool", self.tool_node)
        workflow.add_node("self_healing", self.self_healing_node)

        # Add edges
        workflow.set_entry_point("decision")

        # Decision node can go to tool or end
        workflow.add_conditional_edges(
            "decision", self.should_continue_func, {"continue": "tool", "end": END}
        )

        # Tool node goes to self-healing
        workflow.add_edge("tool", "self_healing")

        # Self-healing goes back to decision
        workflow.add_edge("self_healing", "decision")

        return workflow.compile()


def create_agent_graph(
    decision_node, tool_node, self_healing_node, should_continue_func
) -> StateGraph:
    """
    Create a compiled graph for the agent.

    Args:
        decision_node: The decision node function
        tool_node: The tool node function
        self_healing_node: The self-healing node function
        should_continue_func: The continuation logic function

    Returns:
        Compiled StateGraph ready for execution
    """
    builder = GraphBuilder(decision_node, tool_node, self_healing_node, should_continue_func)
    return builder.build_graph()
