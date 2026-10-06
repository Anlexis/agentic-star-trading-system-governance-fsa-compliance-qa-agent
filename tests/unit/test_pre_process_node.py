# FIN-C2-108 — Unit Tests: PreProcessNode (outer pre_process slot)
#
# Invocation canon: behavioural tests invoke the node via node(state) —
# BaseNode.__call__ -> trust gate -> input mask -> execute() -> output gate.
# PreProcessNode requires VERIFIED_EXTERNAL, so its behavioural tests build
# the state at that level (the ANONYMOUS rejection lives in
# test_trust_gate.py).
#
# Gate layering note: the FRAMEWORK input gate masks user_input /
# validated_input before execute() runs — e-mails, digit-group runs, and
# Title-Case name bigrams (regulatory acronym phrases like "FSA Guidelines"
# or "Financial Instruments" count too) surface as [MASKED]. The NODE's own
# surface strip then catches IBAN-shaped tokens the framework patterns do
# not, and replaces them with [REDACTED]. Intentional-PII tests therefore
# assert the raw identifier is GONE and the corresponding masked marker is
# present.
#
# TestTemplateOwnedScreenDirect calls execute() DIRECTLY (no framework
# wrapper): the template owns its identifier screen, and it must hold even
# in a deployment where no upstream gate ran. This is also where the
# both-direction probes live — real governance sentences with fiscal-year
# rows must SURVIVE the screen byte-identical, while account/card-shaped
# runs are redacted.
#
# Mirrors docs/03_test_spec.md section 2.1 (PRE-01..PRE-09).
# Deterministic — no LLM, no network. framework.* / src.* imports only.

from unittest.mock import MagicMock

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

import src.nodes.pre_process_node
from src.nodes.pre_process_node import PreProcessNode

# Lowercase governance phrasing on purpose: PII-free (no Title-Case bigram,
# no @, no digit run), so the framework input mask leaves the payload untouched.
_VALID_QUERY = "what governance controls apply before deploying a new algorithmic " "trading agent"


def _make_state(user_input=_VALID_QUERY, **extra) -> dict:
    state = {
        "user_input": user_input,
        "caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value,
        "node_history": [],
        "error_log": [],
        "session_id": "unit-session",
        "execution_time": {},
    }
    state.update(extra)
    return state


class TestPreProcessSuccess:
    def test_pre_01_valid_query_accepted(self):
        result = PreProcessNode()(_make_state())
        assert result["status"] == AgentStatus.SUCCESS.value
        # Regression guard: State carries the plain string, never the enum.
        assert type(result["status"]) is str  # noqa: E721 - the enum (a str subclass) must not pass
        assert result["validated_input"] == _VALID_QUERY

    def test_enriched_context_carries_channel(self):
        result = PreProcessNode()(_make_state(input_context={"channel": "web"}))
        assert result["enriched_context"]["channel"] == "web"
        assert result["enriched_context"]["source"] == "TradingGovernanceComplianceAgent"

    def test_missing_channel_defaults_to_unknown(self):
        result = PreProcessNode()(_make_state())
        assert result["enriched_context"]["channel"] == "unknown"


class TestPreProcessRejection:
    def test_pre_02_empty_input_is_error(self):
        result = PreProcessNode()(_make_state(user_input=""))
        assert result["status"] == AgentStatus.ERROR.value
        assert result["error_log"]
        # No validated_input is produced on the reject path.
        assert "validated_input" not in result

    def test_whitespace_only_is_error(self):
        result = PreProcessNode()(_make_state(user_input="   \n\t "))
        assert result["status"] == AgentStatus.ERROR.value

    def test_pre_03_missing_user_input_is_error(self):
        state = _make_state()
        del state["user_input"]
        result = PreProcessNode()(state)
        assert result["status"] == AgentStatus.ERROR.value

    def test_non_string_input_is_error(self):
        result = PreProcessNode()(_make_state(user_input={"malicious": "dict"}))
        assert result["status"] == AgentStatus.ERROR.value


class TestCallerMetadataChannel:
    """The only consumed input_context field is `channel`, locked to an inert
    identifier ([a-z0-9_]{1,32}) — caller-controlled free text never rides it."""

    def test_valid_channel_accepted(self):
        result = PreProcessNode()(_make_state(input_context={"channel": "web_portal"}))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["enriched_context"]["channel"] == "web_portal"

    def test_free_text_channel_is_refused_without_echo(self):
        result = PreProcessNode()(_make_state(input_context={"channel": "Evil <Channel>"}))
        assert result["status"] == AgentStatus.ERROR.value
        assert "validated_input" not in result
        joined = " ".join(str(e) for e in result["error_log"])
        assert "channel" in joined  # the FIELD is named...
        assert "Evil" not in joined  # ...the value is never echoed

    def test_non_string_channel_is_refused(self):
        result = PreProcessNode()(_make_state(input_context={"channel": 123}))
        assert result["status"] == AgentStatus.ERROR.value

    def test_oversized_channel_is_refused(self):
        result = PreProcessNode()(_make_state(input_context={"channel": "a" * 33}))
        assert result["status"] == AgentStatus.ERROR.value

    def test_unconsumed_context_keys_are_ignored(self):
        result = PreProcessNode()(_make_state(input_context={"extra_key": "free text here"}))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["enriched_context"] == {
            "source": "TradingGovernanceComplianceAgent",
            "channel": "unknown",
        }


class TestPreProcessIdentifierScreen:
    """PRE-04..06: raw identifiers never survive into validated_input."""

    def test_iban_redacted_by_node_screen(self):
        # IBAN-shaped tokens are NOT in the framework input-mask patterns —
        # the node's own surface strip must catch them ([REDACTED] path).
        raw = (
            "confirm the operator account tied to kill-switch access "
            "DE89370400440532013000 before granting privileges"
        )
        result = PreProcessNode()(_make_state(user_input=raw))
        vi = result["validated_input"]
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "DE89370400440532013000" not in vi
        assert "[REDACTED]" in vi

    def test_email_masked_by_framework_gate(self):
        # The framework input gate masks e-mail before execute() sees it.
        raw = "escalate the mrm review to compliance.desk@example.com today"
        result = PreProcessNode()(_make_state(user_input=raw))
        vi = result["validated_input"]
        assert "compliance.desk@example.com" not in vi
        assert "[MASKED]" in vi

    def test_grouped_operator_id_digits_masked(self):
        # 4-4-4 digit groups match the framework number patterns.
        raw = "operator id 1234 5678 9012 shows a pending access review flag"
        result = PreProcessNode()(_make_state(user_input=raw))
        vi = result["validated_input"]
        assert "1234 5678 9012" not in vi
        assert "[MASKED]" in vi

    def test_pre_09_title_case_regulatory_bigram_masked(self):
        # Title-Case regulatory-acronym bigrams count as name bigrams for the
        # framework input gate too — "Financial Instruments" is the literal
        # example for this template's domain.
        raw = "please confirm whether Financial Instruments rules require escalation"
        result = PreProcessNode()(_make_state(user_input=raw))
        vi = result["validated_input"]
        assert "Financial Instruments" not in vi
        assert "[MASKED]" in vi


class TestTemplateOwnedScreenDirect:
    """Direct execute() probes — the template's own screen, no framework
    wrapper in front. Both directions: real financial sentences survive,
    identifier-shaped runs are redacted."""

    def _execute(self, user_input, **extra):
        return PreProcessNode().execute(_make_state(user_input=user_input, **extra))

    def test_fiscal_year_rows_survive_byte_identical(self):
        # A run in which every group is a plausible year is governance prose,
        # not an account number — the screen must not fire on it.
        raw = "compare record retention duties across FY 2023 2024 2025 2026 filings"
        result = self._execute(raw)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["validated_input"] == raw

    def test_card_shaped_run_is_redacted(self):
        result = self._execute("customer card 4111 1111 1111 1111 appeared in a complaint")
        vi = result["validated_input"]
        assert "4111 1111 1111" not in vi
        assert "[REDACTED]" in vi

    def test_contiguous_account_run_is_redacted(self):
        result = self._execute("account 1234567890123 flagged in the trading audit")
        vi = result["validated_input"]
        assert "1234567890123" not in vi
        assert "[REDACTED]" in vi

    def test_hyphen_grouped_account_is_redacted(self):
        result = self._execute("reference 1234-5678-9012 in the escalation ticket")
        vi = result["validated_input"]
        assert "1234-5678-9012" not in vi
        assert "[REDACTED]" in vi

    def test_mixed_year_and_nonyear_groups_are_redacted(self):
        # One non-year group is enough to make the run identifier-shaped.
        result = self._execute("code 2023 2024 9912 was pasted into the request")
        vi = result["validated_input"]
        assert "2023 2024 9912" not in vi
        assert "[REDACTED]" in vi

    def test_iban_redacted_without_framework(self):
        result = self._execute("wire from DE89370400440532013000 questioned by audit")
        assert "DE89370400440532013000" not in result["validated_input"]

    def test_email_redacted_without_framework(self):
        result = self._execute("send the mrm summary to compliance.desk@example.com")
        assert "compliance.desk@example.com" not in result["validated_input"]

    def test_bare_years_and_percentages_survive(self):
        raw = "did the 2025 revision raise the 8 percent trigger threshold"
        result = self._execute(raw)
        assert result["validated_input"] == raw


class TestPreProcessAudit:
    def test_pre_08_domain_audit_payload(self, monkeypatch):
        """The accepted request emits pre_process_complete; the assertion
        targets call.args[1] — the event payload — never the whole call repr."""
        spy = MagicMock()
        monkeypatch.setattr(src.nodes.pre_process_node, "emit_trace_event", spy)
        PreProcessNode()(_make_state())
        events = [call.args[0] for call in spy.call_args_list]
        assert "pre_process_complete" in events
        payload = spy.call_args_list[events.index("pre_process_complete")].args[1]
        assert payload["input_chars"] == len(_VALID_QUERY)

    def test_rejection_audit_names_the_field_only(self, monkeypatch):
        spy = MagicMock()
        monkeypatch.setattr(src.nodes.pre_process_node, "emit_trace_event", spy)
        PreProcessNode()(_make_state(input_context={"channel": "Bad Value!"}))
        events = {call.args[0]: call.args[1] for call in spy.call_args_list}
        assert "pre_process_rejected" in events
        payload = events["pre_process_rejected"]
        assert payload == {"reason": "invalid_caller_metadata", "field": "channel"}
