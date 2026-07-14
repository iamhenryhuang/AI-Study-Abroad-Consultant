"""LangGraph topology for the retriever agent."""

from langgraph.graph import END, StateGraph

from .nodes import (
    after_finalize,
    after_verify,
    critic_node,
    decomposer_node,
    experience_search_node,
    extension_function_node,
    finalizer_node,
    fulltext_search_node,
    refiner_node,
    route_to_retrieval,
    searcher_node,
    verifier_node,
)
from .state import AgentState


def _build_graph():
    """Build the existing SQL → fulltext → refine agent workflow."""
    builder = StateGraph(AgentState)

    builder.add_node("decompose", decomposer_node)
    builder.add_node("extension_function", extension_function_node)
    builder.add_node("experience_search", experience_search_node)
    builder.add_node("search", searcher_node)
    builder.add_node("fulltext", fulltext_search_node)
    builder.add_node("verify", verifier_node)
    builder.add_node("refine", refiner_node)
    builder.add_node("finalize", finalizer_node)
    builder.add_node("critic", critic_node)

    builder.set_entry_point("decompose")
    builder.add_conditional_edges("decompose", route_to_retrieval)
    builder.add_conditional_edges("refine", route_to_retrieval)

    builder.add_edge("extension_function", "verify")
    builder.add_edge("experience_search", "verify")
    builder.add_edge("search", "verify")
    builder.add_edge("fulltext", "verify")
    builder.add_conditional_edges("verify", after_verify)
    builder.add_conditional_edges("finalize", after_finalize)
    builder.add_edge("critic", END)

    return builder.compile()


_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = _build_graph()
    return _graph
