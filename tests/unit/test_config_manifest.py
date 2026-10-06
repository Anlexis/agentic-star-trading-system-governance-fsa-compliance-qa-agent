# FIN-C2-108 — Unit Tests: manifest / config consistency
#
# The manifest (config/agent.yaml) is live configuration, not documentation:
# AgentRegistry reads every key at ROOT level, and the declared class path
# must BE the src/graph/graph.py agent class. Runtime parameters live in
# config/config.yaml, whose `retrieval` block
# TradingGovernanceWorkflowGraphNode._parent_config() forwards into the inner
# graph. These tests pin manifest <-> code consistency so a config drift
# fails fast in CI.
#
# Mirrors docs/03_test_spec.md section 2.8 (CFG-01..CFG-07).
# Deterministic — no LLM, no network.

import json
import pathlib

import yaml

from framework.schemas.trust_level import TrustLevel

from src.graph.graph import TradingGovernanceComplianceAgent, TradingGovernanceWorkflowGraphNode
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MANIFEST = yaml.safe_load((_ROOT / "config" / "agent.yaml").read_text(encoding="utf-8"))
_RUNTIME = yaml.safe_load((_ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))


class TestManifestIdentity:
    def test_cfg_01_agent_id_matches_template_id(self):
        assert _MANIFEST["id"] == "FIN-C2-108"

    def test_cfg_02_declared_class_is_the_graph_class(self):
        # Class-name contract: manifest class == graph.py class == server import.
        module_path, _, class_name = _MANIFEST["class"].rpartition(".")
        assert module_path == "src.graph.graph"
        assert class_name == TradingGovernanceComplianceAgent.__name__
        assert _MANIFEST["name"] == TradingGovernanceComplianceAgent().name

    def test_cfg_03_category_and_industry(self):
        assert _MANIFEST["category"] == "Cat 2"
        assert _MANIFEST["industry"] == "FIN"
        assert _MANIFEST["base_type"] == "RAGAgent"
        assert _MANIFEST["namespace"] == "fin"
        assert _MANIFEST["enabled"] is True

    def test_manifest_keys_sit_at_root_level(self):
        # AgentRegistry reads root-level keys only — an `agent:` block would
        # make every declared value invisible to it.
        assert "agent" not in _MANIFEST


class TestManifestSecurity:
    def test_cfg_04_required_trust_level_matches_outer_gate_nodes(self):
        declared = TrustLevel(_MANIFEST["required_trust_level"])
        assert declared is TrustLevel.VERIFIED_EXTERNAL
        assert PreProcessNode.required_trust_level is declared
        assert PostProcessNode.required_trust_level is declared

    def test_cfg_05_max_retry_within_framework_ceiling(self):
        max_retry = _RUNTIME["max_retry"]
        assert isinstance(max_retry, int)
        assert 0 <= max_retry < 10  # AgentBaseGraph MAX_RETRY_CEILING

    def test_requires_gates_match_the_code(self):
        # This template is deterministic and constructs no model client: an
        # extra or secret declared here but never used would fail agent
        # compilation at deploy time.
        assert _MANIFEST["generation_mode"] == "deterministic"
        assert _MANIFEST["requires"]["secrets"] == []
        assert _MANIFEST["requires"]["extras"] == []

    def test_hitl_is_not_enabled(self):
        # This template declares no HITL: no hitl block, no checkpointer.
        assert (_RUNTIME.get("hitl") or {}).get("enabled", False) is False


class TestRetrievalBlock:
    def test_cfg_06_retrieval_block_matches_node_defaults(self):
        # Node module defaults mirror the declared block — a drift silently
        # changes tuning.
        retrieval = _RUNTIME["retrieval"]
        from src.nodes.rerank_filter_node import _DEFAULT_RETRIEVAL as rerank_defaults
        from src.nodes.retrieve_node import _DEFAULT_RETRIEVAL as retrieve_defaults

        assert retrieval["top_k"] == retrieve_defaults["top_k"] == rerank_defaults["top_k"]
        assert (
            retrieval["score_threshold"] == retrieve_defaults["score_threshold"] == rerank_defaults["score_threshold"]
        )
        assert retrieval["kb_path"] == retrieve_defaults["kb_path"]
        assert (_ROOT / retrieval["kb_path"]).is_file()

    def test_cfg_07_parent_config_forwards_the_declared_block(self):
        cfg = TradingGovernanceWorkflowGraphNode()._parent_config()
        assert cfg["configurable"]["retrieval"] == _RUNTIME["retrieval"]
        assert cfg["configurable"]["retrieval"], "_parent_config() must never forward an empty retrieval block"


class TestSeededKnowledgeBase:
    def test_kb_is_a_well_formed_entry_list(self):
        entries = json.loads((_ROOT / _RUNTIME["retrieval"]["kb_path"]).read_text(encoding="utf-8"))
        assert isinstance(entries, list)
        assert len(entries) >= 5, "seeded KB must carry a usable corpus"
        for entry in entries:
            assert set(entry.keys()) == {"id", "title", "category", "source", "tags", "content", "extra"}
            assert entry["id"] and entry["title"] and entry["content"]
            assert entry["category"] in {"ai_mrm", "algo_trading", "fisc", "audit"}
            assert isinstance(entry["extra"], dict)

    def test_kb_ids_are_unique(self):
        entries = json.loads((_ROOT / _RUNTIME["retrieval"]["kb_path"]).read_text(encoding="utf-8"))
        ids = [e["id"] for e in entries]
        assert len(ids) == len(set(ids))

    def test_kb_covers_all_four_structured_product_categories(self):
        # mrm_checklist / circuit_breaker / audit_trail / fisc_mapping each
        # need at least one seeded source category.
        entries = json.loads((_ROOT / _RUNTIME["retrieval"]["kb_path"]).read_text(encoding="utf-8"))
        categories = {e["category"] for e in entries}
        assert categories == {"ai_mrm", "algo_trading", "fisc", "audit"}
