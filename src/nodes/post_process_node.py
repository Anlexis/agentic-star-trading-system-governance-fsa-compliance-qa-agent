"""AgentCore Platform v1.0"""

# FIN-C2-108 - PostProcessNode (outer post_process slot; output content gate)
#
# Reads the final governance answer from state["result"] (populated by
# TradingGovernanceWorkflowGraphNode.merge_output(), mapped from the inner
# graph's formatted_answer output) AND the four structured product fields
# (mrm_checklist, circuit_breaker, audit_trail, fisc_mapping), and applies the
# output content-safety gate to ALL of them before anything is surfaced.
#
# Gate rules: this node calls the MODULE-LEVEL `_security_gate_output()` scan
# from execute() itself - never a `_extra_security_gate_input/_output`
# instance method (the framework auto-wraps those hooks, breaking the
# .invoke() chain). The scan is UNCONDITIONAL: every execute() path runs it
# before any value can be returned, and no flag, argument, or state field can
# suppress it.
#
# The gate scans nested strings RECURSIVELY - not just the top-level rendered
# answer string. A credential-shaped value nested inside a structured
# payload dict/list (e.g. buried in an mrm_checklist requirement string)
# would bypass a top-level-string-only scan; `_iter_nested_strings()` walks
# every dict/list value so that cannot happen here. On a violation, the
# rendered output is replaced with a sanitised stub, ALL FOUR structured
# fields are cleared (fail-closed - defence in depth on top of
# get_output()'s own status-gated surfacing in src/graph/graph.py), and
# AgentStatus.ERROR is returned.

import logging
import re
from typing import Any, ClassVar, Dict, Iterator, List, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import from_json, to_json
from src.services.llm_factory import resolve_llm
from src.services.llm_review import render_review, review_result

logger = logging.getLogger(__name__)

# Disallowed content patterns. Each tuple: (name, compiled regex) —
# order matters (most specific first).
_DISALLOWED_PATTERNS: List[tuple[str, re.Pattern[str]]] = [
    # API key patterns: sk-..., pk-..., ak-...
    ("api_key", re.compile(r"\b(?:sk|pk|ak)-[A-Za-z0-9]{16,}", re.IGNORECASE)),
    # JWT: three base64url segments separated by dots
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    # Bearer token in Authorization-like context
    ("bearer_token", re.compile(r"Bearer\s+[A-Za-z0-9._~+/]{20,}", re.IGNORECASE)),
    # Credential assignment patterns
    (
        "credential_assignment",
        re.compile(
            r"\b(?:password|passwd|secret|api_key|token|access_key|private_key)\s*[:=]\s*\S{8,}",
            re.IGNORECASE,
        ),
    ),
]

_SANITISED_STUB = (
    "[OUTPUT BLOCKED by the output content gate - disallowed content "
    "detected. Review the generated governance answer and retry without "
    "credential-like strings.]"
)


def _iter_nested_strings(value: Any) -> Iterator[str]:
    """Yield every string reachable inside value (dict/list/tuple recursion).

    A scan that only looks at a single top-level string misses a
    credential-shaped value nested inside a returned payload dict or list.
    This walks all of it.
    """
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _iter_nested_strings(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _iter_nested_strings(child)


def _security_gate_output(result_text: str, structured: Dict[str, Any]) -> Optional[str]:
    """Run the output content gate over the rendered answer AND the four
    structured product fields (mrm_checklist / circuit_breaker / audit_trail /
    fisc_mapping, already JSON-decoded by the caller).

    Unconditional: called on every execute() path before any value can be
    returned - there is no suppression flag. Returns the name of the first
    matched violation, or None if everything scanned clean.
    """
    for name, pattern in _DISALLOWED_PATTERNS:
        if pattern.search(result_text):
            return name
    for value in structured.values():
        for text in _iter_nested_strings(value):
            for name, pattern in _DISALLOWED_PATTERNS:
                if pattern.search(text):
                    return name
    return None


class PostProcessNode(FunctionNode):
    """Format and finalize the governance answer, behind the output gate."""

    # Explicit by design, not inherited implicitly. Outer backbone gate slot —
    # matches the manifest's declared required_trust_level (config/agent.yaml).
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> Dict[str, Any]:
        result_text = str(state.get("result") or "")
        structured: Dict[str, Any] = {
            "mrm_checklist": from_json(state.get("mrm_checklist"), []),
            "circuit_breaker": from_json(state.get("circuit_breaker"), {}),
            "audit_trail": from_json(state.get("audit_trail"), []),
            "fisc_mapping": from_json(state.get("fisc_mapping"), []),
        }

        # Output gate — unconditional. Every path through execute() reaches
        # this scan before a value can be returned; there is no suppression
        # flag.
        _llm, _ = resolve_llm(None, state)
        _remarks = review_result(
            _llm,
            user_input=str(state.get("user_input") or ""),
            result=result_text,
            domain="FIN TradingGovernanceComplianceAgent",
        )
        _review = render_review(_remarks)
        # Remarks are LLM text derived from the caller's raw words, so they pass through the
        # same gate the answer does -- appending after the gate would put unscanned text past
        # it. A tripped review is dropped on its own: withholding a correct answer because an
        # advisory remark quoted an identifier would let the review change the outcome, and
        # the whole design rests on it being unable to.
        if _review and isinstance(result_text, str) and not _security_gate_output(result_text + _review, structured):
            result_text = result_text + _review

        violation = _security_gate_output(result_text, structured)
        if violation:
            logger.error(
                "PostProcessNode: OUTPUT BLOCKED - violation type: %s",
                violation,
            )
            return {
                "formatted_output": _SANITISED_STUB,
                "result": _SANITISED_STUB,
                "status": AgentStatus.ERROR.value,
                "error_log": [f"PostProcessNode: output blocked - " f"disallowed content detected ({violation})"],
                # Fail-closed: clear every structured field so no stale
                # compliance data lingers in State on a blocked output.
                # get_output() (src/graph/graph.py) also gates on status, but
                # a node-level clear removes any reliance on that alone.
                "mrm_checklist": to_json([]),
                "circuit_breaker": to_json({"applicable": False}),
                "audit_trail": to_json([]),
                "fisc_mapping": to_json([]),
            }

        # Clean — domain audit: a finalized governance answer was emitted.
        emit_trace_event(
            "post_process_complete",
            {"output_chars": len(result_text)},
            state,
        )

        return {
            "formatted_output": result_text,
            "status": AgentStatus.SUCCESS.value,
        }
