# FIN-C2-108 — Unit Tests: declared runtime config reaches the inner graph
#
# config/config.yaml is live configuration: its `retrieval` block must reach
# RetrieveNode / RerankFilterNode END-TO-END (file -> _parent_config() ->
# DomainWorkflowGraph._extra_initial_state() -> retrieval_config state field
# -> node behaviour), not merely be forwarded one hop. A regression anywhere
# on that chain degrades silently to module defaults — these tests make the
# declared value OBSERVABLE in the rendered output, so a dead-config drift
# fails loudly.
#
# Deterministic — no LLM, no network. framework.* / src.* imports only.

import pathlib

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

import src.graph.graph
from src.graph.graph import Graph, TradingGovernanceWorkflowGraphNode

# Broad governance query — matches several seeded KB entries under the
# declared defaults (top_k 4, score_threshold 0.25).
_BROAD_QUERY = "model validation and monitoring duties for a trading agent"


def _invoke(user_input: str) -> dict:
    ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL, caller_id="unit-suite")
    return Graph().invoke(user_input, ctx=ctx)


def _write_config(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    path = tmp_path / "config.yaml"
    path.write_text(body, encoding="utf-8")
    return path


class TestDeclaredRetrievalReachesTheNodes:
    def test_declared_top_k_bounds_the_citation_list(self, monkeypatch, tmp_path):
        # Baseline under the shipped config: the broad query cites >1 passage.
        baseline = _invoke(_BROAD_QUERY)
        assert baseline["status"] == AgentStatus.SUCCESS.value
        assert "[2]" in baseline["output"], "broad query must cite multiple passages by default"

        # Declare top_k: 1 — the same query must now cite exactly one passage.
        config = _write_config(
            tmp_path,
            "retrieval:\n"
            "  top_k: 1\n"
            "  score_threshold: 0.25\n"
            '  kb_path: "config/kb/trading_governance_kb.json"\n',
        )
        monkeypatch.setattr(src.graph.graph, "_RUNTIME_CONFIG_PATH", config)
        result = _invoke(_BROAD_QUERY)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "[1]" in result["output"]
        assert "[2]" not in result["output"], (
            "declared top_k=1 did not reach RerankFilterNode - the config " "chain is dead"
        )

    def test_declared_score_threshold_reaches_the_filter(self, monkeypatch, tmp_path):
        # A prohibitive threshold filters every passage out: the pipeline
        # degrades to the explicit no-coverage answer.
        config = _write_config(
            tmp_path,
            "retrieval:\n"
            "  top_k: 4\n"
            "  score_threshold: 1.0\n"
            '  kb_path: "config/kb/trading_governance_kb.json"\n',
        )
        monkeypatch.setattr(src.graph.graph, "_RUNTIME_CONFIG_PATH", config)
        result = _invoke(_BROAD_QUERY)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert (
            "does not contain sufficient coverage" in result["output"]
        ), "declared score_threshold=1.0 did not reach RerankFilterNode"

    def test_parent_config_reads_the_declared_file(self, monkeypatch, tmp_path):
        config = _write_config(
            tmp_path,
            'retrieval:\n  top_k: 9\n  score_threshold: 0.5\n  kb_path: "config/kb/trading_governance_kb.json"\n',
        )
        monkeypatch.setattr(src.graph.graph, "_RUNTIME_CONFIG_PATH", config)
        forwarded = TradingGovernanceWorkflowGraphNode()._parent_config()
        assert forwarded["configurable"]["retrieval"]["top_k"] == 9
        assert forwarded["configurable"]["retrieval"]["score_threshold"] == 0.5
