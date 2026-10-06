"""AgentCore Platform v1.0"""

# FIN-C2-108 - DomainWorkflowGraph (inner BaseGraph)
#
# This is the INNER graph for the Cat 2 two-layer nested architecture.
# It encapsulates the full trading-governance / compliance KB search domain
# workflow:
#
#   START -> input_validate -> retrieve -> rerank_filter
#         -> generate_answer -> output_format -> END
#
# Called by TradingGovernanceWorkflowGraphNode.get_subgraph() (graph.py).
# get_output() shapes the sub_result dict consumed by merge_output() there.
#
# Rules enforced:
#   - Inherits BaseGraph (fully custom topology - no forced backbone)
#   - Implements all 7 BaseGraph ABC methods
#   - register_nodes() does NOT call super() (abstract in BaseGraph)
#   - register_nodes() instantiates every domain node with NO ctor args
#   - Does NOT register initialize / finalize (outer backbone concerns)
#   - get_output() designed together with TradingGovernanceWorkflowGraphNode.merge_output()
#   - No platform-internal SDK imports
#   - Not placed under src/subagents/

from typing import Any, Dict

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from src.nodes.generate_answer_node import GenerateAnswerNode
from src.nodes.input_validate_node import InputValidateNode
from src.nodes.output_format_node import OutputFormatNode
from src.nodes.rerank_filter_node import RerankFilterNode
from src.nodes.retrieve_node import RetrieveNode
from src.schemas.state import State, to_json


class DomainWorkflowGraph(BaseGraph):
    """Inner domain workflow graph for FIN-C2-108.

    Inherits BaseGraph directly for a fully custom node topology.
    Called by TradingGovernanceWorkflowGraphNode.get_subgraph() in graph.py,
    which passes the declared runtime config (`_parent_config()`) into the
    ctor.

    Pipeline (linear):
        START
          -> input_validate  (InputValidateNode)  - parse + normalise the question
          -> retrieve        (RetrieveNode)       - keyword-score the seeded KB
          -> rerank_filter   (RerankFilterNode)   - boost / threshold / top_k cut
          -> generate_answer (GenerateAnswerNode) - grounded answer + citations +
                                                     the 4 structured product fields
          -> output_format   (OutputFormatNode)   - final format + mandatory FIEA disclaimer
          -> END

    All nodes are FunctionNode subclasses returning partial-dict state updates.
    initialize / finalize are outer backbone concerns - not registered here.
    """

    # -- Identity --------------------------------------------------------------

    @property
    def name(self) -> str:
        """Unique identifier for this inner graph."""
        return "fin_c2_108_trading_governance_workflow"

    @property
    def state_schema(self) -> type:
        """TypedDict subclass shared across inner and outer graph."""
        return State

    # -- Config validation -----------------------------------------------------

    def _validate_config(self) -> None:
        """Validate inner graph config before compilation.

        The forwarded `retrieval` block (top_k / score_threshold / kb_path) is
        read per-call by the domain nodes with safe defaults, so absence is
        non-fatal. Validation is permissive here rather than raising
        ConfigError.
        """
        pass

    # -- Config forwarding into state (manifest -> inner nodes) -----------------

    def _extra_initial_state(self) -> Dict[str, Any]:
        """Republish the forwarded `retrieval` tuning block into inner state.

        TradingGovernanceWorkflowGraphNode._parent_config() forwards the
        declared `retrieval` block under config["configurable"]; this hook
        makes it reachable by the domain nodes at runtime as the JSON-string
        state field `retrieval_config` (state safety: a JSON string, not a
        bare dict). RetrieveNode / RerankFilterNode read
        this field (module defaults apply when it is absent).
        """
        retrieval = (self.config or {}).get("configurable", {}).get("retrieval") or {}
        return {"retrieval_config": to_json(retrieval)}

    # -- Node registration -----------------------------------------------------

    def register_nodes(self) -> None:
        """Register all 5 domain nodes.

        No super() call - BaseGraph.register_nodes() is abstract.
        Do NOT register initialize or finalize; those are outer backbone
        concerns handled by AgentBaseGraph in graph.py.

        Every node is instantiated with NO constructor arguments:
        FunctionNode subclasses take no __init__;
        config flows in via State (see retrieve_node.py / rerank_filter_node.py
        module docstrings). Every key registered here is referenced in
        add_edges().
        """
        self._nodes["input_validate"] = InputValidateNode()
        self._nodes["retrieve"] = RetrieveNode()
        self._nodes["rerank_filter"] = RerankFilterNode()
        self._nodes["generate_answer"] = GenerateAnswerNode()
        self._nodes["output_format"] = OutputFormatNode()

    # -- Edge wiring -----------------------------------------------------------

    def add_edges(self) -> None:
        """Wire the linear trading-governance domain topology.

        Each step passes its partial-dict output into the shared State.
        For this template the topology is intentionally linear - no conditional
        branching between domain nodes. route() is implemented as required by
        the ABC but add_conditional_edges() is not used.
        """
        self._sg.add_edge(START, "input_validate")
        self._sg.add_edge("input_validate", "retrieve")
        self._sg.add_edge("retrieve", "rerank_filter")
        self._sg.add_edge("rerank_filter", "generate_answer")
        self._sg.add_edge("generate_answer", "output_format")
        self._sg.add_edge("output_format", END)

    # -- Routing ---------------------------------------------------------------

    def route(self, state: AgentState) -> str:
        """Conditional routing - required by BaseGraph ABC.

        For this linear topology add_conditional_edges() is not used, so this
        method is never called at runtime. It is implemented to satisfy the ABC
        contract. Returns END on error so an unexpected call does not re-enter a
        processing node.
        """
        if state.get("status") == AgentStatus.ERROR.value:
            return END
        return "output_format"

    # -- Output shape ----------------------------------------------------------

    def get_output(self, state: AgentState) -> Dict[str, Any]:
        """Shape the output dict returned to the outer graph as sub_result.

        This dict is received by TradingGovernanceWorkflowGraphNode.merge_output()
        in graph.py as the `sub_result` argument. Both methods are designed
        together to guarantee field-name consistency:

            Inner get_output()  emits: "formatted_answer", "citations",
                "mrm_checklist", "circuit_breaker", "audit_trail",
                "fisc_mapping", "status", ...
            Outer merge_output() reads: sub_result.get("formatted_answer"),
                sub_result.get("citations"), sub_result.get("mrm_checklist"),
                sub_result.get("circuit_breaker"), sub_result.get("audit_trail"),
                sub_result.get("fisc_mapping"), sub_result.get("status")

        Additional fields (error_log, intake_notes, trace_id, correlation_id,
        node_history) are surfaced for observability / downstream extension;
        error_log in particular carries an inner refusal reason outward (the
        wrapping GraphNode folds it into the outer error surface).
        """
        return {
            "formatted_answer": state.get("formatted_answer"),
            "citations": state.get("citations"),
            "mrm_checklist": state.get("mrm_checklist"),
            "circuit_breaker": state.get("circuit_breaker"),
            "audit_trail": state.get("audit_trail"),
            "fisc_mapping": state.get("fisc_mapping"),
            "status": state.get("status"),
            "error_log": state.get("error_log", []),
            "intake_notes": state.get("intake_notes"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
        }
