# FIN-C2-108 — Unit Tests: nested Cat-2 graph composition (outer + end-to-end)
#
# Drives the REAL outer agent (TradingGovernanceComplianceAgent / Graph)
# end-to-end via AgentBaseGraph.invoke(). The e2e context is
# InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL) — the
# manifest's declared caller level; for_internal() is NEVER used (it would
# over-privilege the run and hide trust-gate regressions).
#
# Also covers structured-product surfacing: TradingGovernanceComplianceAgent
# .get_output() must EXTEND the base envelope with the four structured
# compliance fields on SUCCESS only, and leave them absent on any ERROR
# (including a trust-gate denial) — fail-closed, never partial/stale.
#
# Mirrors docs/03_test_spec.md section 3 (INT-05..INT-14).
# Deterministic — no LLM, no network. framework.* / src.* imports only.

import pathlib

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

import src.graph.graph
from src.graph.domain_workflow_graph import DomainWorkflowGraph
from src.graph.graph import (
    Graph,
    TradingGovernanceComplianceAgent,
    TradingGovernanceWorkflowGraphNode,
)
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State, from_json, to_json

# Verified offline against the real KB scorer: top-1 kb-001 at score 1.0000,
# wide margin over the next candidate (0.5000) — deterministic e2e query.
_MRM_QUERY = "independent model validation before deploying a trading agent"


def _run(user_input: str, trust: TrustLevel = TrustLevel.VERIFIED_EXTERNAL) -> dict:
    ctx = InvocationContext(caller_trust_level=trust, caller_id="unit-suite")
    return Graph().invoke(user_input, ctx=ctx)


class TestOuterGraphConstruction:
    def test_int_05_inherits_agent_base_graph_directly(self):
        assert issubclass(TradingGovernanceComplianceAgent, AgentBaseGraph)

    def test_int_05_graph_alias(self):
        assert Graph is TradingGovernanceComplianceAgent

    def test_state_schema_is_state(self):
        assert TradingGovernanceComplianceAgent().state_schema is State

    def test_int_06_compile_fills_all_backbone_slots(self):
        agent = TradingGovernanceComplianceAgent()
        agent.compile()
        for slot in ("initialize", "pre_process", "main", "post_process", "finalize"):
            assert agent._nodes.get(slot) is not None, f"backbone slot not filled: {slot}"
        assert isinstance(agent._nodes["pre_process"], PreProcessNode)
        assert isinstance(agent._nodes["main"], TradingGovernanceWorkflowGraphNode)
        assert isinstance(agent._nodes["post_process"], PostProcessNode)

    def test_add_edges_is_not_overridden(self):
        # Backbone wiring belongs to the framework — the template must not
        # redefine it.
        assert "add_edges" not in TradingGovernanceComplianceAgent.__dict__


class TestMainSlotGraphNode:
    def test_int_07_get_subgraph_returns_the_inner_graph(self):
        subgraph = TradingGovernanceWorkflowGraphNode().get_subgraph()
        assert isinstance(subgraph, DomainWorkflowGraph)
        assert subgraph.config["configurable"]["retrieval"], "inner config must carry the retrieval block"

    def test_int_08_extract_input_prefers_validated_input(self):
        node = TradingGovernanceWorkflowGraphNode()
        assert node.extract_input({"validated_input": "VI", "user_input": "UI"}) == "VI"
        assert node.extract_input({"user_input": "UI"}) == "UI"

    def test_int_09_merge_output_maps_the_inner_contract(self):
        node = TradingGovernanceWorkflowGraphNode()
        sub_result = {
            "formatted_answer": "ANSWER",
            "citations": to_json([{"ref": 1, "id": "kb-001", "title": "t", "source": "s"}]),
            "mrm_checklist": to_json([{"item_id": "MRM-1"}]),
            "circuit_breaker": to_json({"applicable": False}),
            "audit_trail": to_json([{"step": "retrieve"}]),
            "fisc_mapping": to_json([]),
            "status": AgentStatus.SUCCESS.value,
        }
        delta = node.merge_output({}, sub_result)
        # The inner formatted_answer surfaces as BOTH governance_answer and
        # result (PostProcessNode's output gate reads state["result"]).
        assert delta == {
            "governance_answer": "ANSWER",
            "result": "ANSWER",
            "citations": sub_result["citations"],
            "mrm_checklist": sub_result["mrm_checklist"],
            "circuit_breaker": sub_result["circuit_breaker"],
            "audit_trail": sub_result["audit_trail"],
            "fisc_mapping": sub_result["fisc_mapping"],
            "status": AgentStatus.SUCCESS.value,
        }

    def test_error_strategy_is_propagate_and_hitl_is_contained(self):
        assert TradingGovernanceWorkflowGraphNode.error_strategy == "propagate"
        assert TradingGovernanceWorkflowGraphNode.propagate_hitl is False

    def test_int_10_parent_config_never_empty_without_config_file(self, monkeypatch):
        # Even with an unreadable runtime-config file the forwarded config
        # carries the fallback retrieval block — never {}.
        monkeypatch.setattr(src.graph.graph, "_RUNTIME_CONFIG_PATH", pathlib.Path("/nonexistent/config.yaml"))
        cfg = TradingGovernanceWorkflowGraphNode()._parent_config()
        assert cfg["configurable"]["retrieval"]["kb_path"] == "config/kb/trading_governance_kb.json"


class TestEndToEndInvoke:
    """Full agent run: outer backbone + inner domain workflow, no LLM."""

    def test_int_11_invoke_returns_success(self):
        result = _run(_MRM_QUERY)
        assert (
            result.get("status") == AgentStatus.SUCCESS.value
        ), f"Expected success, got {result.get('status')}. result={result!r}"

    def test_int_11_output_is_the_gated_formatted_answer(self):
        output = _run(_MRM_QUERY).get("output")
        assert isinstance(output, str) and output.strip()
        assert output.startswith("# Trading Governance & Compliance Answer")
        assert "[1]" in output
        assert "is not legal, compliance, or investment advice" in output

    def test_int_11_e2e_traverses_the_post_process_gate(self):
        history = _run(_MRM_QUERY).get("node_history", [])
        for cls_name in ("PreProcessNode", "TradingGovernanceWorkflowGraphNode", "PostProcessNode"):
            assert cls_name in history, f"node_history missing {cls_name}: {history}"

    def test_no_coverage_query_still_terminates_success(self):
        result = _run("quantum telepathy sandwich recipes")
        assert result.get("status") == AgentStatus.SUCCESS.value
        assert "does not contain sufficient coverage" in result.get("output", "")

    def test_int_12_anonymous_caller_is_denied_at_the_outer_boundary(self):
        """Trust gate at graph level: an ANONYMOUS invoke is refused by the
        VERIFIED_EXTERNAL pre_process slot. The error state short-circuits the
        main slot (its input gate sees status=error and skips the inner graph)
        and routes past post_process to finalize — no domain answer is ever
        produced."""
        result = _run(_MRM_QUERY, trust=TrustLevel.ANONYMOUS)
        assert result.get("status") == AgentStatus.ERROR.value
        assert not result.get("output")
        history = result.get("node_history", [])
        assert "PostProcessNode" not in history
        assert history[:2] == ["InitializeNode", "PreProcessNode"]


class TestStructuredProductSurfacing:
    """get_output() extends the base envelope with the four structured
    compliance fields — SUCCESS-only, fail-closed on any ERROR status."""

    def test_int_13_structured_fields_present_and_typed_on_success(self):
        result = _run(_MRM_QUERY)
        assert result.get("status") == AgentStatus.SUCCESS.value
        # Caller-facing get_output() returns already-parsed Python containers
        # (not the JSON-string State encoding — that's an internal-only form).
        assert isinstance(result.get("mrm_checklist"), list) and result["mrm_checklist"]
        assert result["mrm_checklist"][0]["source_id"] == "kb-001"
        assert isinstance(result.get("circuit_breaker"), dict)
        assert isinstance(result.get("audit_trail"), list) and result["audit_trail"]
        assert isinstance(result.get("fisc_mapping"), list)

    def test_int_14_structured_fields_absent_on_trust_denial(self):
        result = _run(_MRM_QUERY, trust=TrustLevel.ANONYMOUS)
        assert result.get("status") == AgentStatus.ERROR.value
        for key in ("mrm_checklist", "circuit_breaker", "audit_trail", "fisc_mapping"):
            assert key not in result, f"{key} must be absent from an ERROR response (fail-closed)"


class TestStateRoundTrip:
    """State-safety helpers: producers to_json() on write, consumers from_json()."""

    def test_to_from_json_list_round_trip(self):
        original = [{"id": "kb-001", "score": 0.69, "title": "independent model validation"}]
        assert from_json(to_json(original)) == original

    def test_to_from_json_dict_round_trip(self):
        original = {"category": "ai_mrm", "top_k": 3}
        assert from_json(to_json(original)) == original

    def test_to_json_none_passes_through(self):
        assert to_json(None) is None

    def test_from_json_malformed_returns_default(self):
        assert from_json("{not valid json", default=[]) == []
        assert from_json(None, default={}) == {}
        assert from_json("", default=[]) == []
