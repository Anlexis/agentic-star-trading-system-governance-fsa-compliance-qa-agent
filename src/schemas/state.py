"""AgentCore Platform v1.0"""

# State must be a flat TypedDict - never Pydantic BaseModel.
# LangGraph checkpoints use msgpack serialization; Pydantic objects
# cause silent corruption.  Extend AgentState with agent-specific
# fields only.  Do NOT add credentials, secrets, or Pydantic models.
#
# Msgpack safety: structured fields (dict / list[dict]) are stored as JSON
# STRINGS, not bare Python containers - a bare dict/list in a checkpointed
# State field is a state-safety violation. Producers serialize with
# to_json() on write; consumers deserialize with from_json() on read.
#
# FIN-C2-108 - Trading Governance & Compliance Agent (Cat 2 nested RAG).
# Two-layer nested Cat 2 graph: outer backbone (AgentBaseGraph) + inner
# domain workflow (BaseGraph). Fields below cover both layers.
#
# Structured product: mrm_checklist / circuit_breaker / audit_trail /
# fisc_mapping are the deliverable, not incidental metadata. Each is produced
# once by GenerateAnswerNode (deterministic, rule-based over ranked_documents)
# and threaded unchanged through OutputFormatNode -> DomainWorkflowGraph.
# get_output() -> TradingGovernanceWorkflowGraphNode.merge_output() into outer
# State, where TradingGovernanceComplianceAgent.get_output() (src/graph/graph.py)
# whitelists + recursively re-scans them before they ever reach a caller.
#
# PII / confidentiality note: this agent answers governance/compliance
# QUESTIONS about trading systems - it does not process customer PII. Direct
# identifiers accidentally typed into a query are surface-stripped by
# PreProcessNode before any field is written to State.

import json
from typing import Any, NotRequired, Optional

from framework.schemas.agent_state import AgentState


def to_json(value: Any) -> Optional[str]:
    """Serialize a dict/list State field to a JSON string (msgpack safety).

    None passes through unchanged so an 'unset' field stays distinguishable
    from an empty container.
    """
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def from_json(value: Optional[str], default: Any = None) -> Any:
    """Deserialize a JSON-string State field back to its dict/list.

    None / empty / malformed input -> the supplied ``default`` so a missing or
    corrupt field is non-fatal for the consuming node.
    """
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


class State(AgentState):
    """Flat TypedDict for FIN-C2-108.

    All shared fields (user_input, status, session_id, node_history,
    error_log, hitl_*, etc.) are inherited from AgentState.
    Domain fields are NotRequired so the TypedDict is valid at graph
    initialisation, before any node has written a value.
    """

    # ------------------------------------------------------------------
    # Outer layer - set by PreProcessNode / TradingGovernanceWorkflowGraphNode.merge_output
    # ------------------------------------------------------------------

    # Identifier-stripped, validated query payload produced by PreProcessNode.
    # Raw input is NOT persisted beyond PreProcessNode.
    validated_input: NotRequired[str]

    # Final governance answer, mapped from the inner graph's formatted_answer
    # output via merge_output (also mapped to `result`, which PostProcessNode
    # reads for the output gate / outer `output`).
    governance_answer: NotRequired[str]

    # ------------------------------------------------------------------
    # Inner layer - domain nodes (DomainWorkflowGraph)
    # ------------------------------------------------------------------

    # InputValidateNode output
    # Normalised free-text governance question (whitespace-collapsed, length-capped).
    search_query: NotRequired[str]

    # JSON STRING (to_json) of parsed structured query params. Deserialised
    # dict shape: {"category": str | None, "top_k": int | None}.
    # Consumers (RetrieveNode, RerankFilterNode) read it back via from_json().
    query_filters: NotRequired[Optional[str]]

    # Manifest `retrieval` block forwarded by TradingGovernanceWorkflowGraphNode.
    # _parent_config() -> DomainWorkflowGraph._extra_initial_state().
    # JSON STRING (to_json) of {"top_k": int, "score_threshold": float,
    # "kb_path": str}. Consumers (RetrieveNode, RerankFilterNode) read it
    # back via from_json().
    retrieval_config: NotRequired[Optional[str]]

    # RetrieveNode output
    # JSON STRING (to_json) of scored KB candidates. Deserialised shape:
    # list[dict], each entry {"id": str, "title": str, "category": str,
    # "source": str, "score": float, "excerpt": str, "extra": dict}. `extra`
    # carries the category-specific structured payload (mrm_items /
    # circuit_breaker / fisc_control / audit_requirement) copied verbatim from
    # the seeded KB entry - consumed only by GenerateAnswerNode.
    # Consumers (RerankFilterNode) read it back via from_json().
    retrieved_documents: NotRequired[Optional[str]]

    # RerankFilterNode output
    # JSON STRING (to_json) of reranked + threshold-filtered passages, capped
    # at top_k. Same entry shape as retrieved_documents (extra included).
    # Consumers (GenerateAnswerNode) read it back via from_json().
    ranked_documents: NotRequired[Optional[str]]

    # GenerateAnswerNode outputs
    # Rule-assembled grounded answer body with numbered citation markers.
    grounded_answer: NotRequired[str]

    # JSON STRING (to_json) of citations. Deserialised shape: list[dict],
    # each entry {"ref": int, "id": str, "title": str, "source": str}.
    # Consumers (OutputFormatNode) read it back via from_json().
    citations: NotRequired[Optional[str]]

    # Structured product fields - all four are produced ONCE by
    # GenerateAnswerNode, pass unchanged through OutputFormatNode, and are
    # surfaced to the caller ONLY on SUCCESS by
    # TradingGovernanceComplianceAgent.get_output() (whitelisted + recursively
    # re-scanned there; PostProcessNode also recursively scans them).

    # JSON STRING (to_json) of list[dict]. Deserialised entry shape:
    # {"item_id": str, "requirement": str, "guideline_ref": str, "source_id": str}.
    # Empty list when no ranked passage tagged `ai_mrm` supports the query.
    mrm_checklist: NotRequired[Optional[str]]

    # JSON STRING (to_json) of a single dict: {"applicable": bool,
    # "trigger_pct": float | None, "cooling_off_minutes": int | None,
    # "market": str | None, "legal_basis": str | None, "source_id": str | None}.
    # {"applicable": false} when no ranked passage tagged `algo_trading`
    # supports the query.
    circuit_breaker: NotRequired[Optional[str]]

    # JSON STRING (to_json) of list[dict]. Mixed entry shape (unused keys
    # omitted per entry): pipeline-step entries {"step", "detail", "count"};
    # matched audit-requirement KB entries {"step", "control",
    # "retention_years", "legal_basis", "source_id"}. Always non-empty on
    # SUCCESS (records the retrieval/rerank/generate steps taken for this
    # query, independent of whether any `audit` KB passage matched).
    audit_trail: NotRequired[Optional[str]]

    # JSON STRING (to_json) of list[dict]. Deserialised entry shape:
    # {"control_id": str, "control_title": str, "fisc_chapter": str,
    # "source_id": str}. Empty list when no ranked passage tagged `fisc`
    # supports the query.
    fisc_mapping: NotRequired[Optional[str]]

    # OutputFormatNode output
    # Final formatted answer (body + sources + mandatory FIEA disclaimer).
    # Written by OutputFormatNode; surfaced to the outer graph via
    # get_output() -> merge_output().
    formatted_answer: NotRequired[str]

    # Validation / parse notes accumulated during intake (no PII).
    # JSON STRING (to_json) of list[str].
    intake_notes: NotRequired[Optional[str]]

    # ------------------------------------------------------------------
    # Tracing / audit - framework-managed; do NOT write from node code
    # ------------------------------------------------------------------

    trace_id: Optional[str]
    correlation_id: Optional[str]
    # node_history inherited from AgentState; listed here for clarity
    # node_history: Optional[List[str]]
