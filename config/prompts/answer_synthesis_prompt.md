# Answer Synthesis Prompt — FIN-C2-108 (v2 LLM upgrade seam)

> **v1 does NOT use this prompt at runtime.** v1 of `GenerateAnswerNode` is
> deterministic (rule-based grounded assembly over `ranked_documents`); no
> node reads this file. It documents the synthesis contract for the v2 LLM
> upgrade described in `docs/02_design.md` ("v1 Implementation Note — LLM
> synthesis"), so the v2 swap changes only the inside of
> `GenerateAnswerNode.execute()`.
>
> The four structured product fields (`mrm_checklist`, `circuit_breaker`,
> `audit_trail`, `fisc_mapping`) are OUT OF SCOPE for this prompt in v2 as
> well — they stay rule-based, folded deterministically from each passage's
> `extra` payload (see `src/nodes/generate_answer_node.py`). They are
> compliance data extracted from the seeded KB, not prose to be
> LLM-synthesised; an LLM only ever touches `grounded_answer`.

## Contract (v2 GenerateAnswerNode)

- **Input:** the same `ranked_documents` JSON (id / title / category / source /
  score / excerpt / extra) and `search_query` the v1 node reads.
- **Output:** the same state contract — `grounded_answer` (str, with numbered
  `[n]` citation markers) and `citations` (JSON list of
  `{ref, id, title, source}`). `mrm_checklist` / `circuit_breaker` /
  `audit_trail` / `fisc_mapping` are unaffected — still produced by the
  deterministic helpers in this node, not by the LLM call.
- **Grounding rule:** every factual statement in `grounded_answer` must be
  traceable to one of the supplied passages via a `[n]` marker; content not
  present in the passages must not be asserted.
- **No-coverage rule:** when no passage supports the question, say so and
  recommend refining the query or escalating to the compliance team — never
  answer from parametric knowledge.
- **Tone:** neutral, compliance-appropriate, no individualized recommendations
  (the mandatory FIEA disclaimer is appended downstream by `OutputFormatNode`
  — non-suppressible, do not attempt to have the LLM produce or omit it).

## Prompt template

```
You answer trading-governance and compliance questions strictly from the
knowledge-base passages provided below (FSA AI model risk management
guidance, 金融商品取引法 (FIEA) algorithmic-trading provisions, and FISC
安全対策基準 material).

Question:
{search_query}

Passages (each with a reference number):
{ranked_documents}

Rules:
1. Use ONLY the passages above. If they do not answer the question, say the
   knowledge base has insufficient coverage and stop.
2. Mark every factual statement with the [n] reference of its passage.
3. Do not give individualized legal, compliance, or investment advice.
4. Do not restate or summarise the mrm_checklist / circuit_breaker /
   audit_trail / fisc_mapping fields — they are assembled separately.
5. Keep the answer under 300 words.
```

## Manifest coupling

The `llm` block in `config/agent.yaml` (`temperature`, `max_tokens`) is already
forwarded to the inner graph via
`TradingGovernanceWorkflowGraphNode._parent_config()` under
`config["configurable"]["llm"]`; the v2 node reads it from there.
