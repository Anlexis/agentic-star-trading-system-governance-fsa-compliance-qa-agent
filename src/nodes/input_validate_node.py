"""AgentCore Platform v1.0"""

# FIN-C2-108 - InputValidateNode
# Domain node 1: parse and normalise the incoming trading-governance question.
# The outer GraphNode passes validated_input (identifier-stripped by
# PreProcessNode) into the inner graph as its user_input; structured
# parameters travel as a JSON envelope through extract_input() and THIS node
# parses them back:
#
#   plain text            -> the whole string is the governance question
#   {"query": "...",
#    "category": "...",
#    "top_k": N}          -> question + structured filters
#
# `category` restricts retrieval to one KB category: ai_mrm | algo_trading |
# fisc | audit (see config/kb/trading_governance_kb.json).
#
# Injection screen (template-owned, post-parse): this node owns the caller
# parse contract, so it enforces refusal of injection-shaped content ITSELF -
# never relying on an upstream gate alone. The screen runs AFTER json.loads()
# (so \u-escaped payloads are already decoded) and walks every key and string
# value of the parsed envelope, keys included - a hostile field NAME is as
# actionable as a hostile value. Detection is anchored to high-precision
# forms - chat-template control tokens as a token class plus explicit
# override/exfiltration phrases - so ordinary governance prose (questions
# about system prompts, jailbreak testing, SQL storage duties) is never
# refused. On a hit: status ERROR, a class-naming error entry, and NOTHING
# carried forward (no search_query / query_filters written; downstream nodes
# skip on the error status). The matched content is never echoed.
#
# Wired by the inner graph (DomainWorkflowGraph).
# Returns only changed state keys (partial dict).

import json
import math
import re
from typing import Any, ClassVar, Dict, Iterator, List, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import to_json

# Hard cap on the normalised query length (defence-in-depth on input size).
_MAX_QUERY_CHARS = 2000

# Bounds for the caller-supplied top_k override (untrusted numeric guard).
_TOP_K_MIN = 1
_TOP_K_MAX = 20

_WHITESPACE_RE = re.compile(r"\s+")

# Caller category filter is locked to an inert identifier - it is compared
# against KB category labels and must never carry free text.
_CATEGORY_RE = re.compile(r"[a-z0-9_]{1,32}")

# Injection screen patterns. Token-class patterns catch chat-template control
# markers as a family (any <|...|> marker, [INST]/[/INST], <<SYS>>/<</SYS>>,
# bare role tags); phrase patterns are anchored so real governance sentences
# containing the same words do not fire.
_INJECTION_PATTERNS: List[re.Pattern[str]] = [
    # Chat-template control tokens - the token CLASS, not an enumerated list.
    re.compile(r"<\|[^|>\n]{1,32}\|>"),
    re.compile(r"\[/?INST\]", re.IGNORECASE),
    re.compile(r"<</?SYS>>"),
    re.compile(r"<\s*/?(?:system|user|assistant)\s*>", re.IGNORECASE),
    # Explicit instruction-override phrases. A qualifier ("previous", "all",
    # ...) is required so governance prose like "ignore the rules on position
    # limits" or "ignore stale market data" never fires.
    re.compile(
        r"\bignore\s+(?:all\s+|the\s+|any\s+)?(?:previous|prior|above|preceding|"
        r"earlier|system)\s+(?:instruction|prompt|context|message|rule)s?\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bignore\s+all\s+(?:instruction|prompt|rule)s?\b", re.IGNORECASE),
    re.compile(
        r"\bdisregard\s+(?:all\s+|the\s+|any\s+)?(?:previous|prior|above|earlier|system)\b",
        re.IGNORECASE,
    ),
    # Prompt / secret exfiltration aimed at the agent itself.
    re.compile(
        r"\b(?:reveal|print|leak|expose|repeat|show)\s+(?:me\s+)?your\s+"
        r"(?:system\s+)?(?:prompt|instructions?|rules?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:reveal|print|leak|expose|show)\b[^.\n]{0,40}\b"
        r"(?:api[\s_-]?keys?|passwords?|credentials?|secret\s+keys?)\b",
        re.IGNORECASE,
    ),
]


def _iter_screen_texts(value: Any) -> Iterator[str]:
    """Yield every screenable string in a parsed payload - keys included."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str):
                yield key
            yield from _iter_screen_texts(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _iter_screen_texts(child)


def _screen_for_injection(value: Any) -> bool:
    """True if any screenable string matches an injection pattern."""
    for text in _iter_screen_texts(value):
        for pattern in _INJECTION_PATTERNS:
            if pattern.search(text):
                return True
    return False


def _coerce_top_k(value: Any, notes: List[str]) -> Optional[int]:
    """Guarded coercion of the untrusted caller top_k override.

    Finite-and-bounded: bools, non-numerics, non-finite floats and fractional
    floats are dropped with a field-naming note; out-of-range integers are
    clamped into [1, 20]. The rejected value itself is never echoed into a
    note - only the field name and the reason.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        notes.append("InputValidateNode: non-numeric top_k ignored.")
        return None
    if isinstance(value, int):
        top_k = value
    elif isinstance(value, float):
        if not math.isfinite(value) or value != int(value):
            notes.append("InputValidateNode: non-numeric top_k ignored.")
            return None
        top_k = int(value)
    elif isinstance(value, str):
        try:
            top_k = int(value.strip())
        except ValueError:
            notes.append("InputValidateNode: non-numeric top_k ignored.")
            return None
    else:
        notes.append("InputValidateNode: non-numeric top_k ignored.")
        return None
    if top_k < _TOP_K_MIN or top_k > _TOP_K_MAX:
        clamped = max(_TOP_K_MIN, min(_TOP_K_MAX, top_k))
        notes.append(f"InputValidateNode: top_k out of range - clamped to {clamped}.")
        return clamped
    return top_k


class InputValidateNode(FunctionNode):
    """Parse the (possibly JSON-enveloped) request into a normalised question.

    Input state keys:
        validated_input | user_input: identifier-stripped request payload

    Output state keys (partial dict):
        search_query:  normalised free-text governance question
        query_filters: JSON dict {"category": str|None, "top_k": int|None}
        intake_notes:  (when anomalies were seen) JSON list[str]

    On an injection-screen hit nothing is carried forward: status ERROR and a
    class-naming error entry only.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> Dict[str, Any]:
        raw = state.get("validated_input") or state.get("user_input", "")
        notes: List[str] = []

        query = ""
        category: Optional[str] = None
        top_k: Optional[int] = None

        if isinstance(raw, str) and raw.strip():
            payload: Any = None
            text = raw.strip()
            if text.startswith("{"):
                try:
                    payload = json.loads(text)
                except (json.JSONDecodeError, ValueError):
                    notes.append(
                        "InputValidateNode: JSON-looking input did not parse - " "treated as plain text query."
                    )

            # Injection screen - post-parse, keys included. Runs on the parsed
            # envelope when JSON decoded (so escaped payloads are already
            # plain), else on the raw text.
            screen_target: Any = payload if isinstance(payload, dict) else text
            if _screen_for_injection(screen_target):
                emit_trace_event(
                    "input_validate_rejected",
                    {"reason": "injection_screen"},
                    state,
                )
                return {
                    "status": AgentStatus.ERROR.value,
                    "error_log": ["InputValidateNode: request refused - " "injection-pattern content detected"],
                }

            if isinstance(payload, dict):
                query = str(payload.get("query") or payload.get("question") or "")
                raw_category = payload.get("category")
                if isinstance(raw_category, str) and raw_category.strip():
                    candidate = raw_category.strip().lower()
                    if _CATEGORY_RE.fullmatch(candidate):
                        category = candidate
                    else:
                        notes.append("InputValidateNode: category failed validation - " "filter ignored.")
                top_k = _coerce_top_k(payload.get("top_k"), notes)
            else:
                query = text
        else:
            notes.append("InputValidateNode: empty request - no question to search.")

        # Normalise whitespace and cap length.
        query = _WHITESPACE_RE.sub(" ", query).strip()
        if len(query) > _MAX_QUERY_CHARS:
            query = query[:_MAX_QUERY_CHARS]
            notes.append(f"InputValidateNode: query truncated to {_MAX_QUERY_CHARS} chars.")

        filters = {"category": category, "top_k": top_k}

        # Domain audit: request parsed and normalised.
        emit_trace_event(
            "input_validate_complete",
            {
                "query_chars": len(query),
                "has_category_filter": category is not None,
                "has_top_k_override": top_k is not None,
            },
            state,
        )

        out: Dict[str, Any] = {
            "search_query": query,
            "query_filters": to_json(filters),
        }
        if notes:
            out["intake_notes"] = to_json(notes)
        return out
