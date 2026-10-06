"""AgentCore Platform v1.0"""

# FIN-C2-108 - OutputFormatNode
# Domain node 5 (terminal): compose the final formatted answer - the grounded
# answer body, the Sources list, and the MANDATORY FIEA (金融商品取引法)
# advisory disclaimer. The disclaimer is part of THIS node's domain output
# contract, not of the outer post_process slot (post_process only gates, it
# does not compose) - and it is NON-SUPPRESSIBLE: there is no config flag,
# state field, or caller parameter that skips it. It is appended
# unconditionally on every SUCCESS path through this node.
#
# The four structured product fields (mrm_checklist, circuit_breaker,
# audit_trail, fisc_mapping) were already written to State by
# GenerateAnswerNode; this node does not read or re-emit them - the LangGraph
# partial-dict state merge keeps them unchanged through to
# DomainWorkflowGraph.get_output().
#
# Wired by the inner graph (DomainWorkflowGraph). get_output() of the inner
# graph surfaces formatted_answer + status + the structured fields to the
# outer merge_output().
# Returns only changed state keys (partial dict).

from typing import Any, ClassVar, Dict, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import from_json

# MANDATORY FIEA advisory line - appended to EVERY answer this template
# emits. Non-suppressible: no code path in this node omits it.
_FIEA_DISCLAIMER = (
    "This answer is generated from the seeded trading-governance knowledge "
    "base (FSA AI model risk management guidance, 金融商品取引法 (FIEA) "
    "algorithmic-trading provisions, and FISC安全対策基準) for informational "
    "purposes only. It is not legal, compliance, or investment advice, is not "
    "a substitute for review by qualified compliance/legal counsel, and must "
    "be verified against the primary regulatory text before any governance "
    "decision is made."
)


class OutputFormatNode(FunctionNode):
    """Compose the final answer: body + sources + mandatory FIEA disclaimer.

    Input state keys:
        grounded_answer: answer body with [n] citation markers
        citations:       JSON list [{ref, id, title, source}]

    Output state keys (partial dict):
        formatted_answer: final rendered answer string
        status:           AgentStatus.SUCCESS.value (plain string — never
                          the bare enum in State)
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> Dict[str, Any]:
        grounded_answer = state.get("grounded_answer") or ("No answer is available for this request.")
        citations: List[Dict[str, Any]] = from_json(state.get("citations"), []) or []

        lines: List[str] = []
        lines.append("# Trading Governance & Compliance Answer")
        lines.append("")
        lines.append(grounded_answer)
        lines.append("")
        lines.append("## Sources")
        if citations:
            for citation in citations:
                if not isinstance(citation, dict):
                    continue
                ref = citation.get("ref", "?")
                title = str(citation.get("title", "")).strip()
                source = str(citation.get("source", "")).strip()
                suffix = f" ({source})" if source else ""
                lines.append(f"- [{ref}] {title}{suffix}")
        else:
            lines.append("- none (no knowledge-base passage cleared the relevance threshold)")
        lines.append("")
        lines.append("---")
        lines.append("")
        # Non-suppressible: this line is appended unconditionally.
        lines.append(f"*{_FIEA_DISCLAIMER}*")

        formatted_answer = "\n".join(lines)

        # Domain audit: final answer composed (mandatory disclaimer attached).
        emit_trace_event(
            "output_format_complete",
            {
                "answer_chars": len(formatted_answer),
                "citation_count": len(citations),
            },
            state,
        )

        return {
            "formatted_answer": formatted_answer,
            "status": AgentStatus.SUCCESS.value,
        }
