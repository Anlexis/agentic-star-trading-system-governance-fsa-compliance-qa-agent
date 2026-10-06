"""AgentCore Platform v1.0"""

# FIN-C2-108 - Outer graph (AgentBaseGraph; Cat 2 two-layer nested architecture)
#
# Trading Governance & Compliance Agent (Cat 2 RAG domain workflow).
# Deterministic, network-free nested RAG Q&A over a seeded FSA AI-MRM /
# 金商法 (FIEA) algorithmic-trading / FISC安全対策基準 knowledge base.
#
# Architecture (Cat 2):
#
#   Outer backbone (fixed - identical to Cat 1, do NOT override add_edges()):
#     START -> initialize -> pre_process -> main -> {route} -> post_process -> finalize -> END
#                                             |  (RETRY, max 3)
#                                             -> pre_process
#
#   `main` slot is a GraphNode subclass (TradingGovernanceWorkflowGraphNode)
#   that delegates the full trading-governance domain workflow to
#   DomainWorkflowGraph (inner BaseGraph: input_validate -> retrieve ->
#   rerank_filter -> generate_answer -> output_format).
#
#   Domain complexity is fully encapsulated inside the inner graph. The outer
#   backbone is never modified.
#
# Structured product surfacing: get_output() below is OVERRIDDEN to extend
# super().get_output() with the four structured compliance fields
# (mrm_checklist, circuit_breaker, audit_trail, fisc_mapping) - surfaced ONLY
# on SUCCESS, and only after this module's OWN whitelist + recursive
# credential scan (fail-closed: PostProcessNode already gated these fields
# once; this is the second, caller-facing gate - defence in depth, never a
# raw pass-through of a parsed JSON blob).
#
# Directory layout:
#   src/graph/graph.py                 <- outer graph (this file)
#   src/graph/domain_workflow_graph.py <- inner graph
#
# Class-name contract:
#   graph.py class:           TradingGovernanceComplianceAgent (this file)
#   config/agent.yaml class:  "src.graph.graph.TradingGovernanceComplianceAgent"  <- must match
#   src/api/server.py import: from src.graph.graph import TradingGovernanceComplianceAgent
#
# Rules enforced:
#   - TradingGovernanceComplianceAgent inherits AgentBaseGraph (direct framework inheritance)
#   - super().register_nodes() called first (fills initialize + finalize)
#   - TradingGovernanceWorkflowGraphNode assigned to self._nodes["main"]
#   - _parent_config() forwards the declared retrieval tuning (never {})
#   - merge_output() returns only changed keys
#   - add_edges() NOT overridden on the outer graph
#   - No platform-internal SDK imports

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Dict, Iterator, List, cast

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.trust_level import TrustLevel
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State, from_json

if TYPE_CHECKING:
    from src.graph.domain_workflow_graph import DomainWorkflowGraph

# Runtime-parameter file: src/graph/graph.py -> parents[2] = repo root.
# config/agent.yaml is the static manifest (identity + entry point); the
# runtime values - max_retry, timeout_s, and the retrieval tuning block -
# live in config/config.yaml, which the platform registry loads and passes to
# the graph constructor as ``config=`` (the standalone server does the same).
_RUNTIME_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"

# Fallback mirrors the `retrieval` block in config/config.yaml so
# _parent_config() never forwards an empty config even if the file is
# unreadable in an exotic deployment layout.
_FALLBACK_RETRIEVAL = {
    "top_k": 4,
    "score_threshold": 0.25,
    "kb_path": "config/kb/trading_governance_kb.json",
}


def _load_runtime_config() -> Dict[str, Any]:
    """Return the runtime-parameter mapping from config/config.yaml.

    Best-effort: a missing, unreadable or unparseable file yields ``{}`` so
    graph construction never breaks - the retrieval tuning then degrades to
    the module fallback that mirrors the declared values. PyYAML is imported
    lazily: it is a framework runtime dependency, so importing it on demand
    avoids a hard module-load coupling.
    """
    try:
        import yaml

        loaded = yaml.safe_load(_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


class TradingGovernanceWorkflowGraphNode(GraphNode):
    """GraphNode subclass assigned to the `main` slot of the outer agent.

    Wraps DomainWorkflowGraph (inner Cat 2 BaseGraph trading-governance RAG
    pipeline). Called by AgentBaseGraph backbone after pre_process and before
    post_process.

    Contracts:
      get_subgraph()    - instantiate DomainWorkflowGraph with the forwarded
                          runtime config (_parent_config())
      extract_input()   - pull validated_input (identifier-stripped) from outer state
      merge_output()    - map sub_result fields into outer state delta (changed keys only)
      error_strategy    - "propagate": re-raise inner errors as SubgraphError (fail-fast)
    """

    # S-1 declared on the wrapper too: the CI gate only AST-scans FunctionNode
    # subclasses, so a GraphNode main slot passes the pipeline without one and is
    # flagged at review. Same level the nodes in this repo already declare.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    # "propagate": re-raise inner graph exceptions as SubgraphError (default - fail fast).
    # "handle": call on_subgraph_error() instead - use for graceful degradation.
    error_strategy: ClassVar[str] = "propagate"

    # False: HITL interrupts are handled inside the inner graph only.
    # True: surface inner HITL interrupt to the outer caller.
    propagate_hitl: ClassVar[bool] = False

    def _parent_config(self) -> Dict[str, Any]:
        """Forward the declared `retrieval` tuning to the inner graph.

        Loads config/config.yaml and returns the retrieval block under
        config["configurable"] - never an empty dict. The inner graph
        republishes it into inner state
        (DomainWorkflowGraph._extra_initial_state()) so RetrieveNode /
        RerankFilterNode read live top_k / score_threshold values instead of
        dead declarations. Without this hook the inner graph would be built
        with an empty config, leaving every declared tuning value dead on
        arrival.
        """
        runtime = _load_runtime_config()
        retrieval = runtime.get("retrieval")
        if not isinstance(retrieval, dict) or not retrieval:
            retrieval = dict(_FALLBACK_RETRIEVAL)
        return {"configurable": {"retrieval": retrieval}}

    def get_subgraph(self) -> "DomainWorkflowGraph":
        """Instantiate and return the inner domain workflow graph.

        DomainWorkflowGraph is imported lazily (inside the method) to avoid
        circular-import risk at module load time and to match the pattern in
        the Cat 2 sample under src/examples/.

        The inner graph receives the declared runtime config via its BaseGraph
        ctor; its domain NODES still take no constructor arguments and read
        config per-call via State (execute(self, state) -> dict contract - no
        config parameter on any node).
        """
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        return DomainWorkflowGraph(config=self._parent_config())

    def extract_input(self, state: AgentState) -> str:
        """Return the string input passed into inner_graph.invoke().

        PreProcessNode validates and identifier-strips the raw user_input and
        writes the result to validated_input. Prefer that; fall back to
        user_input if validated_input is absent (e.g. in unit tests).
        Structured params (category / top_k) travel inside this string as a
        JSON envelope and are parsed back by the first inner node
        (InputValidateNode).
        """
        return cast(str, state.get("validated_input") or state.get("user_input", ""))

    def merge_output(self, state: AgentState, sub_result: Dict[str, Any]) -> Dict[str, Any]:
        """Map inner graph sub_result back into the outer state delta.

        sub_result is the dict returned by DomainWorkflowGraph.get_output().
        Returns ONLY changed keys - never the full state.

        Key coupling (designed together with DomainWorkflowGraph.get_output()):
          Inner get_output() emits  -> "formatted_answer", "citations",
            "mrm_checklist", "circuit_breaker", "audit_trail", "fisc_mapping",
            "status", ...
          This merge_output() reads -> sub_result.get("formatted_answer"),
            sub_result.get("citations"), sub_result.get("mrm_checklist"),
            sub_result.get("circuit_breaker"), sub_result.get("audit_trail"),
            sub_result.get("fisc_mapping"), sub_result.get("status")

        governance_answer (str | None): final rendered governance answer;
          written by OutputFormatNode inside the inner graph.
        result: PostProcessNode (outer post_process slot) reads
          state.get("result") - the inner graph emits the rendered answer
          under "formatted_answer", so map it to "result" as well; otherwise
          the final output surfaced by PostProcessNode (and the output gate)
          is always empty.
        mrm_checklist / circuit_breaker / audit_trail / fisc_mapping: the
          structured product fields - passed through unchanged (still JSON
          strings) for PostProcessNode's output gate and this class's
          get_output() override to consume.
        status (str | None): terminal AgentStatus value from the inner graph run.
        """
        return {
            "governance_answer": sub_result.get("formatted_answer"),
            "result": sub_result.get("formatted_answer"),
            "citations": sub_result.get("citations"),
            "mrm_checklist": sub_result.get("mrm_checklist"),
            "circuit_breaker": sub_result.get("circuit_breaker"),
            "audit_trail": sub_result.get("audit_trail"),
            "fisc_mapping": sub_result.get("fisc_mapping"),
            "status": sub_result.get("status"),
        }


# ── get_output() override: structured-product surfacing (fail-closed) ─────────
#
# Second, caller-facing output gate (defence in depth on top of
# PostProcessNode's own gate): NEVER return a raw json.loads() blob straight
# from State. Each structured field is rebuilt entry-by-entry from an
# explicit whitelist of scalar keys (ids/numbers/urls/state), then every
# remaining string is recursively re-scanned for credential-shaped content.
# Any residual hit drops ALL FOUR structured keys for this response
# (fail-closed) rather than raising or silently including the bad value.

_MRM_ITEM_FIELDS = ("item_id", "requirement", "guideline_ref", "source_id")
_CIRCUIT_BREAKER_FIELDS = (
    "applicable",
    "trigger_pct",
    "cooling_off_minutes",
    "market",
    "legal_basis",
    "source_id",
)
_AUDIT_TRAIL_FIELDS = (
    "step",
    "detail",
    "count",
    "control",
    "retention_years",
    "legal_basis",
    "source_id",
)
_FISC_MAPPING_FIELDS = ("control_id", "control_title", "fisc_chapter", "source_id")

# Same disallowed-content patterns as PostProcessNode's output gate
# (src/nodes/post_process_node.py) - kept as a small, self-contained,
# module-level copy per file (each gate site owns its own copy rather than
# sharing a cross-file import).
_CREDENTIAL_LIKE_PATTERNS: List[re.Pattern[str]] = [
    re.compile(r"\b(?:sk|pk|ak)-[A-Za-z0-9]{16,}", re.IGNORECASE),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/]{20,}", re.IGNORECASE),
    re.compile(
        r"\b(?:password|passwd|secret|api_key|token|access_key|private_key)\s*[:=]\s*\S{8,}",
        re.IGNORECASE,
    ),
]


def _iter_nested_strings(value: Any) -> Iterator[str]:
    """Yield every string reachable inside value (dict/list/tuple recursion)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _iter_nested_strings(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _iter_nested_strings(child)


def _has_credential_like_string(value: Any) -> bool:
    for text in _iter_nested_strings(value):
        for pattern in _CREDENTIAL_LIKE_PATTERNS:
            if pattern.search(text):
                return True
    return False


def _vet_scalar_dict(entry: Any, allowed_keys: "tuple[str, ...]") -> Dict[str, Any]:
    """Whitelist a dict down to `allowed_keys`, scalar values only.

    Fail-closed: a key passes only if it is in `allowed_keys` AND its value
    is a scalar (str/int/float/bool/None) - never a nested container.
    """
    if not isinstance(entry, dict):
        return {}
    vetted: Dict[str, Any] = {}
    for key in allowed_keys:
        if key not in entry:
            continue
        value = entry[key]
        if value is None or isinstance(value, (str, int, float, bool)):
            vetted[key] = value
    return vetted


def _vet_entry_list(items: Any, allowed_keys: "tuple[str, ...]") -> List[Dict[str, Any]]:
    if not isinstance(items, list):
        return []
    return [_vet_scalar_dict(e, allowed_keys) for e in items if isinstance(e, dict)]


def _build_structured_output(state: AgentState) -> Dict[str, Any]:
    """Rebuild the four structured product fields from State, whitelisted.

    Returns {} (all four keys omitted) if a residual credential-shaped string
    survives the whitelist - fail-closed, never a partial/best-effort surface.
    """
    mrm_checklist = _vet_entry_list(from_json(state.get("mrm_checklist"), []), _MRM_ITEM_FIELDS)
    circuit_breaker = _vet_scalar_dict(from_json(state.get("circuit_breaker"), {}), _CIRCUIT_BREAKER_FIELDS)
    audit_trail = _vet_entry_list(from_json(state.get("audit_trail"), []), _AUDIT_TRAIL_FIELDS)
    fisc_mapping = _vet_entry_list(from_json(state.get("fisc_mapping"), []), _FISC_MAPPING_FIELDS)

    candidate = {
        "mrm_checklist": mrm_checklist,
        "circuit_breaker": circuit_breaker,
        "audit_trail": audit_trail,
        "fisc_mapping": fisc_mapping,
    }
    if any(_has_credential_like_string(v) for v in candidate.values()):
        # Fail-closed: PostProcessNode should already have caught this: a
        # residual hit here means never surface any of the four fields.
        return {}
    return candidate


class TradingGovernanceComplianceAgent(AgentBaseGraph):
    """Outer graph for FIN-C2-108 (Cat 2 RAG, structured compliance product).

    Inherits AgentBaseGraph directly (framework base class). Domain logic is
    fully encapsulated in TradingGovernanceWorkflowGraphNode (main slot),
    which delegates to DomainWorkflowGraph (inner BaseGraph).

    Backbone (fixed - identical to Cat 1):
        START -> initialize -> pre_process -> main -> post_process -> finalize -> END

    register_nodes() is the ONLY backbone-wiring override:
      - super().register_nodes() fills: initialize, finalize (framework defaults)
      - pre_process:  PreProcessNode (input validation + identifier strip)
      - main:         TradingGovernanceWorkflowGraphNode (delegates to DomainWorkflowGraph)
      - post_process: PostProcessNode (output content gate)

    get_output() is additionally overridden to EXTEND super().get_output()
    with the four structured compliance fields - see the module-level
    "get_output() override" section above.

    add_edges() is NOT overridden - backbone wiring belongs to the framework.
    """

    @property
    def name(self) -> str:
        """Agent identifier registered with AgentRegistry."""
        return "TradingGovernanceComplianceAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        """Fill all 5 backbone slots.

        super().register_nodes() MUST be called first - it injects the
        framework's default InitializeNode (sets schema_version, session_id,
        trust_level) and FinalizeNode (builds response_metadata, total_time_ms).
        """
        super().register_nodes()  # fills: initialize, finalize

        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = TradingGovernanceWorkflowGraphNode()
        self._nodes["post_process"] = PostProcessNode()

    # add_edges() is NOT overridden - backbone wiring belongs to the framework.

    def get_output(self, state: AgentState) -> Dict[str, Any]:
        """Extend the base envelope with the four structured product fields.

        Calls super().get_output() first (never replaces it -
        status/trace_id/correlation_id/node_history/output all stay intact),
        then surfaces mrm_checklist / circuit_breaker / audit_trail /
        fisc_mapping ONLY when the run succeeded. Fail-closed: on any
        non-SUCCESS status (including a PostProcessNode output-gate block) the
        four keys are simply absent from the response - never a partial or
        stale value.
        """
        base: Dict[str, Any] = super().get_output(state)
        if base.get("status") != AgentStatus.SUCCESS.value:
            return base
        structured = _build_structured_output(state)
        return {**base, **structured}


# Back-compat alias - config/agent.yaml declares the dotted class path
# "src.graph.graph.TradingGovernanceComplianceAgent", and src/api/server.py
# imports the class directly. Keep both names pointing at the agent.
Graph = TradingGovernanceComplianceAgent
