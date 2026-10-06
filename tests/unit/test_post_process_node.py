# FIN-C2-108 — Unit Tests: PostProcessNode (outer post_process slot; output gate)
#
# Invocation canon: node(state) via BaseNode.__call__. PostProcessNode is the
# second outer gate slot and requires VERIFIED_EXTERNAL (like PreProcessNode),
# so its behavioural tests build the state at that level; the ANONYMOUS
# rejection lives in test_trust_gate.py.
#
# Gate layering: the node's own module-level _security_gate_output() scan runs
# INSIDE execute() and replaces a violating answer with the sanitised stub
# (returned dict — no exception). The framework's FunctionNode credential
# scan then sees only the clean stub. Intentional-credential tests assert the
# raw secret never survives into formatted_output OR result.
#
# RECURSIVE FORM (a defect class this repo deliberately guards against): the
# gate scans NESTED strings recursively across `result` AND the four
# structured product fields (mrm_checklist / circuit_breaker / audit_trail /
# fisc_mapping), not just a single top-level string.
# TestRecursiveStructuredFieldScan below proves a credential buried INSIDE a
# structured field (list-of-dicts and a bare dict) is caught and the output
# blocked fail-closed — the exact defect class a top-level-only scan would
# miss.
#
# Mirrors docs/03_test_spec.md section 2.7 (POST-01..POST-08).
# Deterministic — no LLM, no network. framework.* / src.* imports only.

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.nodes.post_process_node import PostProcessNode
from src.schemas.state import from_json, to_json

_CLEAN_REPORT = (
    "# Trading Governance & Compliance Answer\n\n" "[1] independent model validation is required before deployment.\n"
)

# JWT-shaped token built at runtime so no credential-shaped literal ever sits
# in the repository (CI credential-scan hygiene).
_FAKE_JWT = "eyJ" + "a" * 12 + "." + "b" * 12 + "." + "c" * 12


def _make_state(result_text, **extra) -> dict:
    state = {
        "result": result_text,
        "caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value,
        "node_history": [],
        "error_log": [],
        "session_id": "unit-session",
        "execution_time": {},
    }
    state.update(extra)
    return state


class TestPostProcessClean:
    def test_post_01_clean_output_passes_through(self):
        result = PostProcessNode()(_make_state(_CLEAN_REPORT))
        assert result["status"] == AgentStatus.SUCCESS.value
        # Regression guard: State carries the plain string, never the enum.
        assert type(result["status"]) is str  # noqa: E721 - the enum (a str subclass) must not pass
        assert result["formatted_output"] == _CLEAN_REPORT

    def test_post_02_empty_result_is_non_fatal(self):
        result = PostProcessNode()(_make_state(""))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["formatted_output"] == ""

    def test_post_03_clean_structured_fields_pass_through_untouched(self):
        mrm = to_json(
            [{"item_id": "MRM-1", "requirement": "clean item", "guideline_ref": "ref", "source_id": "kb-001"}]
        )
        result = PostProcessNode()(_make_state(_CLEAN_REPORT, mrm_checklist=mrm))
        assert result["status"] == AgentStatus.SUCCESS.value
        # A clean run does not rewrite the structured fields at all.
        assert "mrm_checklist" not in result


class TestPostProcessOutputGate:
    def _assert_blocked(self, result, secret):
        assert result["status"] == AgentStatus.ERROR.value
        assert any("output blocked" in str(e) for e in result["error_log"])
        # The raw secret must not survive into either surfaced field.
        assert secret not in str(result.get("formatted_output", ""))
        assert secret not in str(result.get("result", ""))
        assert result["formatted_output"].startswith("[OUTPUT BLOCKED")

    def test_post_04_api_key_is_blocked(self):
        secret = "sk-ABCDEF0123456789abcdef"
        result = PostProcessNode()(_make_state(f"# Report\n\n<!-- debug api_key={secret} -->\n"))
        self._assert_blocked(result, secret)

    def test_post_05_credential_assignment_is_blocked(self):
        secret = "password=super_secret_value_123"
        result = PostProcessNode()(_make_state(f"# Report\n\ninternal note: {secret}\n"))
        self._assert_blocked(result, "super_secret_value_123")

    def test_post_06_jwt_is_blocked(self):
        result = PostProcessNode()(_make_state(f"# Report\n\nsession token {_FAKE_JWT}\n"))
        self._assert_blocked(result, _FAKE_JWT)

    def test_post_07_bearer_token_is_blocked(self):
        secret = "Bearer abcdefghijklmnopqrstuvwxyz0123456789"
        result = PostProcessNode()(_make_state(f"# Report\n\nauthorization: {secret}\n"))
        self._assert_blocked(result, secret)


class TestRecursiveStructuredFieldScan:
    """Proves the output gate scans NESTED strings recursively — a credential
    buried inside a structured product field (not the top-level `result`
    string) must still be caught, fail-closed. A top-level-only scan is the
    exact defect class this repo deliberately guards against; these tests
    prove the fix, not just assume it.
    """

    def test_credential_nested_inside_mrm_checklist_list_is_caught(self):
        secret = "sk-ZZZZZZ0123456789abcdefzz"
        # 2 levels deep: list -> dict -> string value. The top-level `result`
        # string is completely clean on its own.
        mrm = to_json(
            [
                {
                    "item_id": "MRM-1",
                    "requirement": "a clean requirement",
                    "guideline_ref": "ref-1",
                    "source_id": "kb-001",
                },
                {
                    "item_id": "MRM-2",
                    "requirement": f"leaked credential {secret} in requirement text",
                    "guideline_ref": "ref-2",
                    "source_id": "kb-002",
                },
            ]
        )
        result = PostProcessNode()(_make_state(_CLEAN_REPORT, mrm_checklist=mrm))
        assert result["status"] == AgentStatus.ERROR.value
        assert any("output blocked" in str(e) for e in result["error_log"])
        assert secret not in str(result)
        # Fail-closed: ALL FOUR structured fields are cleared, not just the
        # one that carried the violation.
        assert from_json(result["mrm_checklist"]) == []
        assert from_json(result["circuit_breaker"]) == {"applicable": False}
        assert from_json(result["audit_trail"]) == []
        assert from_json(result["fisc_mapping"]) == []

    def test_credential_nested_inside_circuit_breaker_dict_is_caught(self):
        secret = "Bearer zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"
        # 1 level deep: dict -> string value (legal_basis field).
        cb = to_json(
            {
                "applicable": True,
                "trigger_pct": 8.0,
                "cooling_off_minutes": 15,
                "market": "TSE cash equities",
                "legal_basis": f"internal note: {secret}",
                "source_id": "kb-006",
            }
        )
        result = PostProcessNode()(_make_state(_CLEAN_REPORT, circuit_breaker=cb))
        assert result["status"] == AgentStatus.ERROR.value
        assert secret not in str(result)
        assert from_json(result["circuit_breaker"]) == {"applicable": False}
        assert from_json(result["mrm_checklist"]) == []

    def test_clean_top_level_result_does_not_mask_a_dirty_structured_field(self):
        """Regression guard for a top-level-only scan: `result` alone is
        squeaky clean, so a scanner that only looked at `result` would PASS
        this case — the recursive scan must not."""
        secret = "api_key=abcdefghij0123456789"
        audit = to_json(
            [
                {"step": "retrieve", "detail": "kb_candidates_scored", "count": 4},
                {
                    "step": "audit_requirement_match",
                    "control": f"leaked {secret}",
                    "retention_years": 7,
                    "legal_basis": "ref",
                    "source_id": "kb-013",
                },
            ]
        )
        result = PostProcessNode()(_make_state("a completely clean rendered answer.", audit_trail=audit))
        assert result["status"] == AgentStatus.ERROR.value
        assert secret not in str(result)
