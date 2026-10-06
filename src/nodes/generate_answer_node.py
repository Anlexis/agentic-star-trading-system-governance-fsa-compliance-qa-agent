"""AgentCore Platform v1.0"""

# FIN-C2-108 - GenerateAnswerNode
# Domain node 4 (structured-product generation node): assembles
# the grounded answer AND the four structured deliverables - mrm_checklist,
# circuit_breaker, audit_trail, fisc_mapping - from the ranked KB passages.
# This is the ONLY node that produces the four structured fields; OutputFormatNode
# only formats the text answer and passes the structured fields through
# unchanged (they persist in State via the LangGraph partial-dict merge).
#
# v1 is DETERMINISTIC (no live LLM call, NO network call): every field is
# built by rule from `ranked_documents` only - a lead sentence plus one cited
# point per passage for grounded_answer (numbered [n] citation markers), and a
# deterministic fold/aggregate of each passage's `extra` payload for the four
# structured fields. Nothing outside ranked_documents reaches any output field,
# so every field is grounded by construction. The LLM synthesis upgrade seam
# is documented in docs/02_design.md ("v1 Implementation Note - LLM
# synthesis") and config/prompts/answer_synthesis_prompt.md: a v2 node swaps
# the grounded_answer assembly for an LLM call over the same input and emits
# the same state contract; the four structured fields stay rule-based in v2
# too (they are compliance data, not prose to be re-synthesised).
#
# Wired by the inner graph (DomainWorkflowGraph).
# Returns only changed state keys (partial dict).

from typing import Any, ClassVar, Dict, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import from_json, to_json

# Answer body used when no KB passage cleared the relevance threshold.
_NO_COVERAGE_ANSWER = (
    "The trading-governance knowledge base does not contain sufficient "
    "coverage to answer this question. Rephrase the query with more specific "
    "MRM, algorithmic-trading, or FISC terms, or escalate to the compliance "
    "team for a manual review."
)

# Cited excerpt length per passage inside the answer body.
_POINT_EXCERPT_CHARS = 240

# KB `category` values that feed each structured field.
_MRM_CATEGORY = "ai_mrm"
_CIRCUIT_BREAKER_CATEGORY = "algo_trading"
_FISC_CATEGORY = "fisc"
_AUDIT_CATEGORY = "audit"

_DEFAULT_CIRCUIT_BREAKER: Dict[str, Any] = {
    "applicable": False,
    "trigger_pct": None,
    "cooling_off_minutes": None,
    "market": None,
    "legal_basis": None,
    "source_id": None,
}


def _first_sentences(text: str, limit: int) -> str:
    """Trim an excerpt at a sentence boundary where possible, else hard-cap."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    period = cut.rfind(". ")
    if period > limit // 2:
        return cut[: period + 1]
    return cut.rstrip() + "..."


def _build_mrm_checklist(ranked: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten every `ai_mrm` passage's checklist items, tagged with its source."""
    checklist: List[Dict[str, Any]] = []
    for doc in ranked:
        if str(doc.get("category", "")) != _MRM_CATEGORY:
            continue
        extra = doc.get("extra", {})
        items = extra.get("mrm_items", []) if isinstance(extra, dict) else []
        doc_id = str(doc.get("id", ""))
        for item in items:
            if not isinstance(item, dict):
                continue
            checklist.append(
                {
                    "item_id": str(item.get("item_id", "")),
                    "requirement": str(item.get("requirement", "")),
                    "guideline_ref": str(item.get("guideline_ref", "")),
                    "source_id": doc_id,
                }
            )
    return checklist


def _build_circuit_breaker(ranked: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Take the circuit-breaker block from the top-ranked `algo_trading` passage.

    ranked_documents is already score-sorted (RerankFilterNode), so the first
    algo_trading match is the most relevant one for this question.
    """
    for doc in ranked:
        if str(doc.get("category", "")) != _CIRCUIT_BREAKER_CATEGORY:
            continue
        extra = doc.get("extra", {})
        cb = extra.get("circuit_breaker") if isinstance(extra, dict) else None
        if not isinstance(cb, dict):
            continue
        return {
            "applicable": True,
            "trigger_pct": cb.get("trigger_pct"),
            "cooling_off_minutes": cb.get("cooling_off_minutes"),
            "market": cb.get("market"),
            "legal_basis": cb.get("legal_basis"),
            "source_id": str(doc.get("id", "")),
        }
    return dict(_DEFAULT_CIRCUIT_BREAKER)


def _build_fisc_mapping(ranked: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten every `fisc` passage's control mapping, tagged with its source."""
    mapping: List[Dict[str, Any]] = []
    for doc in ranked:
        if str(doc.get("category", "")) != _FISC_CATEGORY:
            continue
        extra = doc.get("extra", {})
        fc = extra.get("fisc_control") if isinstance(extra, dict) else None
        if not isinstance(fc, dict):
            continue
        mapping.append(
            {
                "control_id": str(fc.get("control_id", "")),
                "control_title": str(fc.get("control_title", "")),
                "fisc_chapter": str(fc.get("fisc_chapter", "")),
                "source_id": str(doc.get("id", "")),
            }
        )
    return mapping


def _build_audit_trail(ranked: List[Dict[str, Any]], retrieved_count: int, query: str) -> List[Dict[str, Any]]:
    """Deterministic audit trail: pipeline steps taken + any matched `audit` KB entries.

    Always non-empty on SUCCESS - it records what THIS run did, independent of
    whether the question happened to match an `audit`-category passage.
    """
    trail: List[Dict[str, Any]] = [
        {
            "step": "retrieve",
            "detail": "kb_candidates_scored",
            "count": retrieved_count,
        },
        {
            "step": "rerank_filter",
            "detail": "kb_candidates_ranked",
            "count": len(ranked),
        },
    ]
    for doc in ranked:
        if str(doc.get("category", "")) != _AUDIT_CATEGORY:
            continue
        extra = doc.get("extra", {})
        ar = extra.get("audit_requirement") if isinstance(extra, dict) else None
        if not isinstance(ar, dict):
            continue
        trail.append(
            {
                "step": "audit_requirement_match",
                "control": str(ar.get("control", "")),
                "retention_years": ar.get("retention_years"),
                "legal_basis": str(ar.get("legal_basis", "")),
                "source_id": str(doc.get("id", "")),
            }
        )
    trail.append(
        {
            "step": "generate_answer",
            "detail": "grounded_answer_assembled",
            "count": len(ranked),
        }
    )
    return trail


class GenerateAnswerNode(FunctionNode):
    """Rule-based grounded answer + structured compliance-product assembly.

    Input state keys:
        ranked_documents:    JSON list of surviving passages (from RerankFilterNode)
        retrieved_documents: JSON list of pre-rerank candidates (audit_trail count only)
        search_query:        normalised question (for the lead sentence)

    Output state keys (partial dict):
        grounded_answer:  answer body with [n] citation markers
        citations:        JSON list [{ref, id, title, source}]
        mrm_checklist:    JSON list[dict] (structured product field)
        circuit_breaker:  JSON dict (structured product field)
        audit_trail:      JSON list[dict] (structured product field)
        fisc_mapping:      JSON list[dict] (structured product field)
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> Dict[str, Any]:
        ranked: List[Dict[str, Any]] = from_json(state.get("ranked_documents"), []) or []
        retrieved: List[Dict[str, Any]] = from_json(state.get("retrieved_documents"), []) or []
        query = state.get("search_query") or ""

        citations: List[Dict[str, Any]] = []

        if not ranked:
            grounded_answer = _NO_COVERAGE_ANSWER
        else:
            lines: List[str] = []
            if query:
                lines.append(
                    f"Based on the seeded trading-governance knowledge base, the "
                    f'following passages answer the question: "{query}"'
                )
            else:
                lines.append(
                    "Based on the seeded trading-governance knowledge base, the " "most relevant passages are:"
                )
            lines.append("")
            for ref, doc in enumerate(ranked, start=1):
                if not isinstance(doc, dict):
                    continue
                title = str(doc.get("title", "")).strip()
                excerpt = _first_sentences(str(doc.get("excerpt", "")), _POINT_EXCERPT_CHARS)
                lines.append(f"[{ref}] {title}: {excerpt}")
                citations.append(
                    {
                        "ref": ref,
                        "id": str(doc.get("id", "")),
                        "title": title,
                        "source": str(doc.get("source", "")),
                    }
                )
            grounded_answer = "\n".join(lines)

        mrm_checklist = _build_mrm_checklist(ranked)
        circuit_breaker = _build_circuit_breaker(ranked)
        fisc_mapping = _build_fisc_mapping(ranked)
        audit_trail = _build_audit_trail(ranked, len(retrieved), query)

        # Domain audit: grounded answer + structured product assembled.
        # Payload carries counts/flags only - never passage or answer text.
        emit_trace_event(
            "generate_answer_complete",
            {
                "citation_count": len(citations),
                "answer_chars": len(grounded_answer),
                "no_coverage": not ranked,
                "mrm_checklist_items": len(mrm_checklist),
                "circuit_breaker_applicable": circuit_breaker.get("applicable", False),
                "fisc_mapping_items": len(fisc_mapping),
            },
            state,
        )

        return {
            "grounded_answer": grounded_answer,
            "citations": to_json(citations),
            "mrm_checklist": to_json(mrm_checklist),
            "circuit_breaker": to_json(circuit_breaker),
            "audit_trail": to_json(audit_trail),
            "fisc_mapping": to_json(fisc_mapping),
        }
