# FIN-C2-108 — Unit Tests: GenerateAnswerNode (inner domain node 4)
#
# Invocation canon: node(state) via BaseNode.__call__ with an ANONYMOUS caller.
# The grounded answer / citations / structured fields are DOMAIN fields (not
# framework input-mask scan targets), so Title-Case KB titles inside them are
# safe to assert on.
#
# This is the canonical structured-product generation node:
# beyond the grounded answer + citations (GEN-01..05, mirroring the golden
# shape), it is the ONLY node that folds ranked_documents[].extra into the
# four compliance deliverables — mrm_checklist / circuit_breaker /
# audit_trail / fisc_mapping. Candidates are synthetic dicts (not real KB
# retrieval), fully deterministic and isolated from RetrieveNode.
#
# Mirrors docs/03_test_spec.md section 2.5 (GEN-01..GEN-12).
# Deterministic — rule-assembled from ranked_documents only (grounded by
# construction; no LLM, no network). framework.* / src.* imports only.

from framework.schemas.trust_level import TrustLevel

from src.nodes.generate_answer_node import GenerateAnswerNode
from src.schemas.state import from_json, to_json


def _ranked(*entries):
    return to_json(list(entries))


def _doc(doc_id, title, excerpt, category="ai_mrm", source="seeded kb", extra=None):
    return {
        "id": doc_id,
        "title": title,
        "category": category,
        "source": source,
        "score": 0.9,
        "excerpt": excerpt,
        "extra": extra or {},
    }


def _make_state(ranked_documents, query="governance controls for trading agents", **extra) -> dict:
    state = {
        "ranked_documents": ranked_documents,
        "search_query": query,
        "caller_trust_level": TrustLevel.ANONYMOUS.value,
        "node_history": [],
        "error_log": [],
        "session_id": "unit-session",
        "execution_time": {},
    }
    state.update(extra)
    return state


class TestGroundedAnswer:
    def test_gen_01_answer_carries_numbered_citation_markers(self):
        ranked = _ranked(
            _doc(
                "kb-001",
                "Independent model validation before deploying a trading AI agent",
                "an independent function must validate the model.",
                category="ai_mrm",
            ),
            _doc(
                "kb-006",
                "Circuit breaker and kill-switch controls for automated order flow",
                "automated trading systems must implement a kill switch.",
                category="algo_trading",
            ),
        )
        result = GenerateAnswerNode()(_make_state(ranked))
        answer = result["grounded_answer"]
        assert "[1] Independent model validation before deploying a trading AI agent:" in answer
        assert "[2] Circuit breaker and kill-switch controls for automated order flow:" in answer

    def test_gen_02_lead_sentence_quotes_the_query(self):
        ranked = _ranked(_doc("kb-001", "Independent model validation", "excerpt."))
        result = GenerateAnswerNode()(_make_state(ranked, query="governance controls for trading agents"))
        assert 'the question: "governance controls for trading agents"' in result["grounded_answer"]

    def test_gen_03_citations_mirror_ranked_order(self):
        ranked = _ranked(
            _doc("kb-001", "Independent model validation", "a.", source="FSA AI-MRM overview"),
            _doc("kb-006", "Circuit breaker controls", "b.", category="algo_trading"),
        )
        citations = from_json(GenerateAnswerNode()(_make_state(ranked))["citations"])
        assert [c["ref"] for c in citations] == [1, 2]
        assert [c["id"] for c in citations] == ["kb-001", "kb-006"]
        assert citations[0]["source"] == "FSA AI-MRM overview"

    def test_citations_is_json_string(self):
        # State safety: list-shaped State fields travel as JSON strings.
        ranked = _ranked(_doc("kb-001", "Independent model validation", "a."))
        result = GenerateAnswerNode()(_make_state(ranked))
        assert isinstance(result["citations"], str)

    def test_gen_04_answer_is_grounded_in_ranked_passages_only(self):
        ranked = _ranked(
            _doc("kb-001", "Independent model validation", "benchmark decision quality against a held-out dataset.")
        )
        answer = GenerateAnswerNode()(_make_state(ranked))["grounded_answer"]
        assert "benchmark decision quality against a held-out dataset." in answer
        assert "[2]" not in answer


class TestMrmChecklist:
    """B10: mrm_checklist folds every ai_mrm-category doc's extra.mrm_items."""

    def test_gen_05_single_ai_mrm_doc_flattens_its_items(self):
        extra = {
            "mrm_items": [
                {
                    "item_id": "MRM-1",
                    "requirement": "Independent validation before go-live.",
                    "guideline_ref": "FSA AI-MRM overview",
                },
                {
                    "item_id": "MRM-2",
                    "requirement": "Benchmark results recorded before deployment.",
                    "guideline_ref": "FSA AI-MRM overview",
                },
            ]
        }
        ranked = _ranked(_doc("kb-001", "Independent model validation", "excerpt.", category="ai_mrm", extra=extra))
        mrm = from_json(GenerateAnswerNode()(_make_state(ranked))["mrm_checklist"])
        assert mrm == [
            {
                "item_id": "MRM-1",
                "requirement": "Independent validation before go-live.",
                "guideline_ref": "FSA AI-MRM overview",
                "source_id": "kb-001",
            },
            {
                "item_id": "MRM-2",
                "requirement": "Benchmark results recorded before deployment.",
                "guideline_ref": "FSA AI-MRM overview",
                "source_id": "kb-001",
            },
        ]

    def test_gen_06_non_ai_mrm_doc_contributes_nothing(self):
        ranked = _ranked(_doc("kb-010", "Access control", "excerpt.", category="fisc", extra={"fisc_control": {}}))
        mrm = from_json(GenerateAnswerNode()(_make_state(ranked))["mrm_checklist"])
        assert mrm == []


class TestCircuitBreaker:
    """B10: circuit_breaker takes the FIRST (top-ranked) algo_trading doc's block."""

    def test_gen_07_top_ranked_algo_trading_doc_wins_over_a_later_one(self):
        ranked = _ranked(
            _doc(
                "kb-006",
                "Circuit breaker controls",
                "a.",
                category="algo_trading",
                extra={
                    "circuit_breaker": {
                        "trigger_pct": 8.0,
                        "cooling_off_minutes": 15,
                        "market": "TSE cash equities",
                        "legal_basis": "basis-a",
                    }
                },
            ),
            _doc(
                "kb-007",
                "Order-to-trade ratio limits",
                "b.",
                category="algo_trading",
                extra={
                    "circuit_breaker": {
                        "trigger_pct": 99.0,
                        "cooling_off_minutes": 60,
                        "market": "should not win",
                        "legal_basis": "basis-b",
                    }
                },
            ),
        )
        cb = from_json(GenerateAnswerNode()(_make_state(ranked))["circuit_breaker"])
        assert cb == {
            "applicable": True,
            "trigger_pct": 8.0,
            "cooling_off_minutes": 15,
            "market": "TSE cash equities",
            "legal_basis": "basis-a",
            "source_id": "kb-006",
        }

    def test_gen_08_no_algo_trading_doc_yields_default_not_applicable(self):
        ranked = _ranked(_doc("kb-001", "Independent model validation", "a.", category="ai_mrm"))
        cb = from_json(GenerateAnswerNode()(_make_state(ranked))["circuit_breaker"])
        assert cb == {
            "applicable": False,
            "trigger_pct": None,
            "cooling_off_minutes": None,
            "market": None,
            "legal_basis": None,
            "source_id": None,
        }


class TestFiscMapping:
    """B10: fisc_mapping folds EVERY fisc-category doc (unlike circuit_breaker's first-only rule)."""

    def test_gen_09_multiple_fisc_docs_all_contribute(self):
        ranked = _ranked(
            _doc(
                "kb-010",
                "Access control",
                "a.",
                category="fisc",
                extra={
                    "fisc_control": {
                        "control_id": "FISC-4-2",
                        "control_title": "Access control and privileged operation logging",
                        "fisc_chapter": "Facility & Technology standards, access control (overview)",
                    }
                },
            ),
            _doc(
                "kb-009",
                "System risk management",
                "b.",
                category="fisc",
                extra={
                    "fisc_control": {
                        "control_id": "FISC-3-1",
                        "control_title": "System change management",
                        "fisc_chapter": "Facility & Technology standards, system risk management (overview)",
                    }
                },
            ),
        )
        mapping = from_json(GenerateAnswerNode()(_make_state(ranked))["fisc_mapping"])
        assert mapping == [
            {
                "control_id": "FISC-4-2",
                "control_title": "Access control and privileged operation logging",
                "fisc_chapter": "Facility & Technology standards, access control (overview)",
                "source_id": "kb-010",
            },
            {
                "control_id": "FISC-3-1",
                "control_title": "System change management",
                "fisc_chapter": "Facility & Technology standards, system risk management (overview)",
                "source_id": "kb-009",
            },
        ]


class TestAuditTrail:
    """B10: audit_trail always carries the 2 pipeline steps + generate_answer,
    plus one audit_requirement_match entry per audit-category ranked doc."""

    def test_gen_10_baseline_steps_plus_audit_match(self):
        ranked = _ranked(
            _doc(
                "kb-013",
                "Trade order audit log retention",
                "a.",
                category="audit",
                extra={
                    "audit_requirement": {
                        "control": "Trade order audit log retention",
                        "retention_years": 7,
                        "legal_basis": "FIEA record-keeping rules (overview)",
                    }
                },
            ),
        )
        state = _make_state(ranked, retrieved_documents=to_json([{"id": f"c-{i}"} for i in range(5)]))
        trail = from_json(GenerateAnswerNode()(state)["audit_trail"])
        assert trail == [
            {"step": "retrieve", "detail": "kb_candidates_scored", "count": 5},
            {"step": "rerank_filter", "detail": "kb_candidates_ranked", "count": 1},
            {
                "step": "audit_requirement_match",
                "control": "Trade order audit log retention",
                "retention_years": 7,
                "legal_basis": "FIEA record-keeping rules (overview)",
                "source_id": "kb-013",
            },
            {"step": "generate_answer", "detail": "grounded_answer_assembled", "count": 1},
        ]

    def test_gen_11_baseline_only_when_no_audit_category_doc(self):
        ranked = _ranked(_doc("kb-001", "Independent model validation", "a.", category="ai_mrm"))
        state = _make_state(ranked, retrieved_documents=to_json([]))
        trail = from_json(GenerateAnswerNode()(state)["audit_trail"])
        assert trail == [
            {"step": "retrieve", "detail": "kb_candidates_scored", "count": 0},
            {"step": "rerank_filter", "detail": "kb_candidates_ranked", "count": 1},
            {"step": "generate_answer", "detail": "grounded_answer_assembled", "count": 1},
        ]
        # Always non-empty on SUCCESS, independent of an audit-category match.
        assert len(trail) >= 1


class TestNoCoverage:
    def test_gen_12_empty_ranked_set_yields_no_coverage_answer(self):
        result = GenerateAnswerNode()(_make_state(_ranked()))
        assert "does not contain sufficient coverage" in result["grounded_answer"]
        assert from_json(result["citations"]) == []
        assert from_json(result["mrm_checklist"]) == []
        assert from_json(result["circuit_breaker"]) == {
            "applicable": False,
            "trigger_pct": None,
            "cooling_off_minutes": None,
            "market": None,
            "legal_basis": None,
            "source_id": None,
        }
        assert from_json(result["fisc_mapping"]) == []

    def test_missing_ranked_field_is_treated_as_no_coverage(self):
        state = _make_state(None)
        del state["ranked_documents"]
        result = GenerateAnswerNode()(state)
        assert "does not contain sufficient coverage" in result["grounded_answer"]
