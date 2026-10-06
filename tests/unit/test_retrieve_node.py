# FIN-C2-108 — Unit Tests: RetrieveNode (inner domain node 2)
#
# Invocation canon: node(state) via BaseNode.__call__ with an ANONYMOUS
# caller — ALWAYS, no exceptions (there is no config-argument
# execute(state, config=...) direct-call carve-out: this node's execute()
# takes NO config parameter at all — a 2nd positional argument TypeErrors).
# Retrieval tuning is exercised by SEEDING the state field `retrieval_config`
# (the JSON string DomainWorkflowGraph._extra_initial_state() republishes
# from the forwarded declared block), never by passing a config argument.
#
# Query scores below were verified offline against the actual tokenizer +
# per-field-weight scorer in src/nodes/retrieve_node.py (title 1.0 > tags 0.8
# > content 0.5, averaged over non-stopword query tokens) run against the
# real seeded config/kb/trading_governance_kb.json — not hand-guessed.
#
# Mirrors docs/03_test_spec.md section 2.3 (RET-01..RET-09).
# Deterministic — keyword scoring over the seeded KB; no LLM, no network.
# framework.* / src.* imports only.

from framework.schemas.trust_level import TrustLevel

from src.nodes.retrieve_node import RetrieveNode
from src.schemas.state import from_json, to_json

# Verified top-1 = kb-001 (score 1.0000), clear margin over kb-016 (0.5000).
_AI_MRM_QUERY = "independent model validation before deploying a trading agent"

# Verified top-1 (fisc-filtered pool) = kb-010 (score 1.0000), margin over
# kb-009 (0.2000).
_FISC_QUERY = "access control and privileged operation logging"


def _make_state(query=_AI_MRM_QUERY, **extra) -> dict:
    state = {
        "search_query": query,
        "caller_trust_level": TrustLevel.ANONYMOUS.value,
        "node_history": [],
        "error_log": [],
        "session_id": "unit-session",
        "execution_time": {},
    }
    state.update(extra)
    return state


class TestRetrieveHappyPath:
    def test_ret_01_top_hit_is_expected_mrm_entry(self):
        result = RetrieveNode()(_make_state())
        docs = from_json(result["retrieved_documents"])
        assert docs, "expected candidates for the ai_mrm query"
        assert docs[0]["id"] == "kb-001"

    def test_ret_02_scores_sorted_descending(self):
        docs = from_json(RetrieveNode()(_make_state())["retrieved_documents"])
        scores = [d["score"] for d in docs]
        assert scores == sorted(scores, reverse=True)
        assert all(s > 0.0 for s in scores)

    def test_ret_03_entry_shape_and_excerpt_cap(self):
        docs = from_json(RetrieveNode()(_make_state())["retrieved_documents"])
        for doc in docs:
            assert set(doc.keys()) == {"id", "title", "category", "source", "score", "excerpt", "extra"}
            assert len(doc["excerpt"]) <= 400
            assert isinstance(doc["extra"], dict)

    def test_ret_04_extra_payload_is_carried_verbatim(self):
        # kb-001's extra.mrm_items is the structured payload GenerateAnswerNode
        # folds into the mrm_checklist structured product field — must survive
        # retrieval untouched.
        docs = from_json(RetrieveNode()(_make_state())["retrieved_documents"])
        top = docs[0]
        assert top["id"] == "kb-001"
        assert "mrm_items" in top["extra"]
        assert len(top["extra"]["mrm_items"]) == 2

    def test_retrieved_documents_is_json_string(self):
        # State safety: list-shaped State fields travel as JSON strings.
        result = RetrieveNode()(_make_state())
        assert isinstance(result["retrieved_documents"], str)


class TestRetrieveFilters:
    def test_ret_05_category_filter_restricts_pool(self):
        state = _make_state(
            query=_FISC_QUERY,
            query_filters=to_json({"category": "fisc", "top_k": None}),
        )
        docs = from_json(RetrieveNode()(state)["retrieved_documents"])
        assert docs, "fisc category has seeded entries"
        assert {d["category"] for d in docs} == {"fisc"}
        assert docs[0]["id"] == "kb-010"

    def test_ret_06_empty_query_yields_no_candidates(self):
        docs = from_json(RetrieveNode()(_make_state(query=""))["retrieved_documents"])
        assert docs == []

    def test_ret_07_out_of_domain_query_yields_no_candidates(self):
        docs = from_json(RetrieveNode()(_make_state(query="quantum telepathy sandwich recipes"))["retrieved_documents"])
        assert docs == []


class TestRetrieveConfigPrecedence:
    """State-seeded config only (there is no config-argument
    carve-out for this repo's node signatures) — every call below still goes
    through node(state) / __call__, never node.execute(state, config=...)."""

    def test_ret_08_state_retrieval_config_kb_path_override(self):
        state = _make_state(retrieval_config=to_json({"kb_path": "config/kb/does_not_exist.json"}))
        result = RetrieveNode()(state)
        assert from_json(result["retrieved_documents"]) == []
        notes = from_json(result.get("intake_notes"), [])
        assert any("not readable" in n for n in notes)

    def test_ret_09_module_defaults_used_when_retrieval_config_absent(self):
        # No retrieval_config key at all (e.g. a bare unit-test state) —
        # falls back to the module defaults mirroring config/agent.yaml.
        state = _make_state()
        assert "retrieval_config" not in state
        docs = from_json(RetrieveNode()(state)["retrieved_documents"])
        assert docs, "module defaults must resolve the real seeded KB path"


class TestRetrieveNotesAccumulation:
    def test_notes_append_never_clobber(self):
        state = _make_state(
            intake_notes=to_json(["earlier note from input validation"]),
            retrieval_config=to_json({"kb_path": "config/kb/bogus.json"}),
        )
        result = RetrieveNode()(state)
        notes = from_json(result["intake_notes"])
        assert notes[0] == "earlier note from input validation"
        assert len(notes) == 2
