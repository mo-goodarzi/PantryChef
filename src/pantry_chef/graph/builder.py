"""Wire the nodes into the conversation graph.

START -> load_profile -> [safety_question -> safety_confirm]* -> parse_request -> search
search -> search (retry with verifier feedback, max 3 attempts) -> [quantity_check] ->
present -> [ask_video] -> respond -> END; quantity_check goes back to search when the
amounts rule out every option; "show me more" goes from present back to search;
ask_video only with a YouTube key.
"""

from functools import partial

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from pantry_chef.graph import nodes
from pantry_chef.graph.nodes import ChatDeps, ChatNodes
from pantry_chef.graph.state import ChatState


def build_graph(
    deps: ChatDeps, checkpointer: BaseCheckpointSaver | None = None
) -> CompiledStateGraph:
    n = ChatNodes(deps)
    graph = StateGraph(ChatState)
    graph.add_node("load_profile", n.load_profile)
    graph.add_node("safety_question", n.safety_question)
    graph.add_node("safety_confirm", n.safety_confirm)
    graph.add_node("parse_request", n.parse_request)
    graph.add_node("search", n.search)
    graph.add_node("quantity_check", n.quantity_check)
    graph.add_node("present", n.present)
    graph.add_node("ask_video", n.ask_video)
    graph.add_node("respond", n.respond)

    graph.add_edge(START, "load_profile")
    graph.add_conditional_edges(
        "load_profile", nodes.after_load_profile, ["safety_question", "parse_request"]
    )
    graph.add_edge("safety_question", "safety_confirm")
    graph.add_conditional_edges(
        "safety_confirm", nodes.after_safety_confirm, ["safety_question", "parse_request"]
    )
    graph.add_conditional_edges("parse_request", nodes.after_parse_request, ["search", END])
    graph.add_conditional_edges(
        "search",
        partial(nodes.after_search, ask_quantities=deps.quantity_question != "off"),
        ["search", "quantity_check", "present"],
    )
    graph.add_conditional_edges("quantity_check", nodes.after_quantity_check, ["search", "present"])
    graph.add_conditional_edges(
        "present",
        partial(nodes.after_present, offer_video=deps.video is not None),
        ["ask_video", "respond", "search", END],
    )
    graph.add_edge("ask_video", "respond")
    graph.add_edge("respond", END)
    return graph.compile(checkpointer=checkpointer)
