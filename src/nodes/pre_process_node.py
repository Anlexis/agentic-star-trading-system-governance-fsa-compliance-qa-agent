"""AgentCore Platform v1.0"""

# FIN-C2-108 - PreProcessNode (outer pre_process slot; input validation + identifier screen)
#
# Node contract: extend FunctionNode; implement execute(state) -> dict with NO
# extra parameters. Return ONLY the fields this node changes (never full
# state). Read input_context via state.get("input_context", {}) - read-only.
# Never import from mediator/, api/, or other agents.
#
# Input validation: reject empty / malformed input before the inner domain
# workflow runs. Identifier screen: a surface screen redacts direct
# identifiers (account numbers, IBANs, e-mail) from the free-text question
# payload before validated_input is written, so raw identifiers never reach
# the inner domain nodes or the checkpoint DB. This agent answers
# governance/compliance QUESTIONS about trading systems (not customer
# transactions), but a caller may still paste an identifier by mistake - the
# screen is defence-in-depth either way.
#
# Caller metadata: the only input_context field this template consumes is
# `channel` - a short transport label recorded in enriched_context. It is
# locked to an inert identifier ([a-z0-9_]{1,32}) so caller-controlled free
# text cannot ride that field; any other input_context key is ignored
# entirely (never parsed, rendered, or persisted by this template's nodes).

import re
from typing import Any, ClassVar, Dict, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

# Surface-level identifier patterns redacted before validated_input is written.
# Downstream domain nodes only ever operate on the normalised question text
# and KB passage summaries, never raw account/customer identifiers.
_PII_PATTERNS: List[re.Pattern[str]] = [
    # IBAN: 2 letters + 2 digits + up to 30 alphanumerics.
    re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"),
    # E-mail addresses.
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
]

# Grouped bank / account / card numbers: three digit groups (4 + 4 + 2-11),
# optionally separated by a hyphen or space. Guarded against fiscal-year rows:
# governance questions legitimately contain runs of 4-digit years
# ("compare FY 2023 2024 2025 2026 retention duties"), which are number-grouped
# exactly like a card number - a run in which EVERY group is a plausible year
# is left intact; anything else redacts. Contiguous 10-19 digit runs have no
# year reading and always redact.
_ACCOUNT_GROUPED_RE = re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{2,11}\b")
_YEAR_TOKEN_RE = re.compile(r"(?:19|20)\d{2}")
_GROUP_SPLIT_RE = re.compile(r"[- ]")

_PII_REPLACEMENT = "[REDACTED]"

# The only caller-metadata field consumed by this template. Inert identifier:
# lowercase alphanumerics/underscore, 1-32 chars - free text never rides here.
_CHANNEL_RE = re.compile(r"[a-z0-9_]{1,32}")


def _redact_account_shaped_runs(text: str) -> str:
    """Redact grouped digit runs unless the run reads as fiscal years only."""

    def _sub(match: "re.Match[str]") -> str:
        groups = _GROUP_SPLIT_RE.split(match.group(0))
        if all(_YEAR_TOKEN_RE.fullmatch(group) for group in groups):
            return match.group(0)  # fiscal-year row, not an account number
        return _PII_REPLACEMENT

    return _ACCOUNT_GROUPED_RE.sub(_sub, text)


def _surface_strip_identifiers(text: str) -> str:
    """Redact obvious direct-identifier tokens from a free-text string."""
    for pattern in _PII_PATTERNS:
        text = pattern.sub(_PII_REPLACEMENT, text)
    return _redact_account_shaped_runs(text)


class PreProcessNode(FunctionNode):
    """Input validation + identifier screen before main processing.

    Rejects empty / invalid input before the inner domain workflow graph runs,
    surface-strips direct account / customer identifiers from the payload, and
    locks the caller-metadata `channel` field to an inert identifier.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> Dict[str, Any]:
        user_input = state.get("user_input", "")
        input_context = state.get("input_context", {})  # read-only

        if not user_input or not isinstance(user_input, str) or not user_input.strip():
            emit_trace_event(
                "pre_process_rejected",
                {"reason": "empty_input"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["PreProcessNode: user_input is empty or missing"],
                # The runner surfaces `formatted_output or result` as `output`. A reason left only in
                # error_log reaches no one: the terminal result carries just `status`, and get_output()
                # does not copy error_log out of the graph -- the caller sees a blank spinner.
                "formatted_output": "Request could not be completed. "
                + ("PreProcessNode: user_input is empty or missing"),
            }

        # Caller metadata: `channel` is locked to an inert identifier. The
        # rejection names the field, never the value (rejected values are not
        # echoed into logs or state).
        if not isinstance(input_context, dict):
            input_context = {}
        channel = input_context.get("channel", "unknown")
        if not (isinstance(channel, str) and _CHANNEL_RE.fullmatch(channel)):
            if "channel" in input_context:
                emit_trace_event(
                    "pre_process_rejected",
                    {"reason": "invalid_caller_metadata", "field": "channel"},
                    state,
                )
                return {
                    "status": AgentStatus.ERROR.value,
                    "error_log": ["PreProcessNode: input_context field 'channel' failed validation"],
                    # The runner surfaces `formatted_output or result` as `output`. A reason left only in
                    # error_log reaches no one: the terminal result carries just `status`, and get_output()
                    # does not copy error_log out of the graph -- the caller sees a blank spinner.
                    "formatted_output": "Request could not be completed. "
                    + ("PreProcessNode: input_context field 'channel' failed validation"),
                }
            channel = "unknown"

        validated_input = _surface_strip_identifiers(user_input.strip())

        # Domain audit: a governance question was accepted and
        # surface-redacted (no direct identifiers in the payload).
        emit_trace_event(
            "pre_process_complete",
            {"input_chars": len(validated_input)},
            state,
        )

        return {
            "validated_input": validated_input,
            "enriched_context": {
                "source": "TradingGovernanceComplianceAgent",
                "channel": channel,
            },
            "status": AgentStatus.SUCCESS.value,
        }
