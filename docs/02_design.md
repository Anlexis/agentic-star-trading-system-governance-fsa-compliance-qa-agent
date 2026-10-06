# Template Design Specification — FIN-C2-108

**Template ID:** FIN-C2-108
**Template Name:** TradingGovernanceComplianceAgent
**Category:** Cat 2 (multi-step domain workflow — RAG pattern)
**Industry:** FIN

## Position in the Framework Architecture

| Aspect | Value |
|---|---|
| L1 Base (framework base class) | `AgentBaseGraph` — direct framework inheritance (Cat 2 nested: outer `AgentBaseGraph` + inner `BaseGraph`) |
| Agent class | `TradingGovernanceComplianceAgent` (alias `Graph`, `src/graph/graph.py`) |
| Inner graph | `DomainWorkflowGraph` (`BaseGraph`, `src/graph/domain_workflow_graph.py`) |
| Pattern | Cat 2 two-layer nested architecture (outer fixed 5-node backbone + `GraphNode` in the `main` slot wrapping an inner domain workflow) |
| State | flat `TypedDict` composition (no Pydantic — msgpack incompatible); structured fields stored as JSON strings via `to_json()` / `from_json()` |
| Nodes | framework `FunctionNode` subclasses — `execute(self, state) -> dict` override only, no extra parameters |
| Graph wiring | composition (`register_nodes()` for node substitution); outer `add_edges()` is NOT overridden |

## Purpose

Trading-governance / compliance knowledge-base search agent: financial-institution
IT, compliance, and internal-audit staff ask natural-language questions about
multi-agent trading-system governance and receive a cited, KB-grounded answer
PLUS four structured compliance deliverables — a per-agent FSA AI Model Risk
Management (MRM) checklist, applicable circuit-breaker controls, an audit
trail of how the answer was assembled, and a FISC安全対策基準 control mapping
— synthesized within the canonical generation/output nodes, always carrying a
mandatory FIEA (金融商品取引法) disclaimer. v1 is fully deterministic and
network-free (keyword retrieval over a seeded/stubbed KB + rule-based
structured-field assembly; no live LLM call, no vector store, no external
network call — see the v1 Implementation Note below).

## Architecture Overview

### Outer backbone (AgentBaseGraph)

```
START → initialize → pre_process → main → {route} → post_process → finalize → END
                                     ↓ (retry, max 3)
                                   pre_process
```

| Slot | Class | Responsibility | required_trust_level |
|------|-------|----------------|----------------------|
| initialize | InitializeNode (framework default) | session_id, trust_level, schema_version | — (framework) |
| pre_process | `PreProcessNode` | validate non-empty input; surface-strip direct identifiers (account numbers, IBAN, e-mail) → `validated_input`; lock the caller-metadata `channel` field to an inert identifier | `TrustLevel.VERIFIED_EXTERNAL` |
| main | `TradingGovernanceWorkflowGraphNode` (`GraphNode`) | delegates to inner `DomainWorkflowGraph`; maps inner `formatted_answer` + the 4 structured fields → outer `result` / `governance_answer` / `mrm_checklist` / `circuit_breaker` / `audit_trail` / `fisc_mapping` | — (GraphNode delegation) |
| post_process | `PostProcessNode` | output gate — module-level `_security_gate_output()` recursively scans `result` AND all 4 structured fields for credentials → ERROR + sanitised stub + cleared structured fields (fail-closed) | `TrustLevel.VERIFIED_EXTERNAL` |
| finalize | FinalizeNode (framework default) | response_metadata, total_time_ms | — (framework) |

`TradingGovernanceComplianceAgent.get_output()` is ADDITIONALLY overridden
(structured-product surfacing — see the dedicated section below).

### Inner graph (DomainWorkflowGraph — BaseGraph, linear)

```
START → input_validate → retrieve → rerank_filter → generate_answer → output_format → END
```

All five inner domain nodes declare `required_trust_level = TrustLevel.ANONYMOUS`
(the external trust gate lives on the outer backbone gate node; a stricter inner
level would deny a real VERIFIED_EXTERNAL invoke at runtime).

| Node | Responsibility | required_trust_level | Input State | Output State |
|------|----------------|----------------------|-------------|--------------|
| `InputValidateNode` | Parse the (possibly JSON-enveloped) question; refuse injection-shaped content (post-parse screen, keys included); normalise whitespace; cap length; guard numeric params (`top_k` 1–20, finite-and-bounded); lock the `category` filter to an inert identifier | `TrustLevel.ANONYMOUS` | `validated_input` \| `user_input` | `search_query`, `query_filters`, `intake_notes` |
| `RetrieveNode` | Deterministic keyword retrieval over the seeded/stubbed KB (`config/kb/trading_governance_kb.json`): tokenise query, score title/tags/content overlap, apply category filter; carries each entry's structured `extra` payload through | `TrustLevel.ANONYMOUS` | `search_query`, `query_filters`, `retrieval_config` | `retrieved_documents`, `intake_notes` |
| `RerankFilterNode` | Rerank candidates (category-match boost), drop entries below `score_threshold`, cap at `top_k`; `extra` passes through unchanged | `TrustLevel.ANONYMOUS` | `retrieved_documents`, `query_filters`, `retrieval_config` | `ranked_documents` |
| `GenerateAnswerNode` | **Canonical generation node.** Rule-based grounded answer assembly with numbered citations, PLUS deterministic fold of `ranked_documents[].extra` into the 4 structured product fields | `TrustLevel.ANONYMOUS` | `ranked_documents`, `retrieved_documents`, `search_query` | `grounded_answer`, `citations`, `mrm_checklist`, `circuit_breaker`, `audit_trail`, `fisc_mapping` |
| `OutputFormatNode` | **Canonical output node.** Compose the final answer: body + Sources list + the MANDATORY, non-suppressible FIEA disclaimer (disclaimer is part of this node, NOT post_process); structured fields pass through unchanged via State merge | `TrustLevel.ANONYMOUS` | `grounded_answer`, `citations` | `formatted_answer`, `status` |

### Data Flow

```
user_input
  → PreProcessNode (validate + identifier strip)  → validated_input
  → TradingGovernanceWorkflowGraphNode.extract_input → inner DomainWorkflowGraph.invoke(validated_input)
        → input_validate                         → search_query / query_filters
        → retrieve                               → retrieved_documents (each candidate carries `extra`)
        → rerank_filter                          → ranked_documents (`extra` unchanged)
        → generate_answer                        → grounded_answer / citations /
                                                     mrm_checklist / circuit_breaker /
                                                     audit_trail / fisc_mapping
        → output_format                          → formatted_answer (+ mandatory FIEA disclaimer)
     get_output() → {formatted_answer, citations, mrm_checklist, circuit_breaker,
                      audit_trail, fisc_mapping, status, ...}
  → TradingGovernanceWorkflowGraphNode.merge_output → result / governance_answer /
                                                        mrm_checklist / circuit_breaker /
                                                        audit_trail / fisc_mapping
  → PostProcessNode (recursive scan over result + all 4 structured fields) → formatted_output (gated)
  → TradingGovernanceComplianceAgent.get_output() (override) → caller-facing
        output + mrm_checklist / circuit_breaker / audit_trail / fisc_mapping
        (SUCCESS-only, whitelisted, recursively re-scanned — fail-closed)
```

Structured parameters travel as JSON through `extract_input()`: when the caller
supplies a JSON envelope (`{"query": ..., "category": ..., "top_k": ...}`), it
passes through `validated_input` as a string and the FIRST inner node
(`InputValidateNode`) parses it back. Inner nodes read their input via
`state.get("validated_input") or state.get("user_input", "")`.

## Configuration Contract

Two files, two roles:

- **`config/agent.yaml`** — the static registry manifest. Every key sits at
  ROOT level (identity, `class` as a single dotted import path,
  `required_trust_level`, `generation_mode`, and the compile-time `requires`
  gates). This template is deterministic and constructs no model client, so
  `requires.secrets` and `requires.extras` are both `[]` — declaring an
  unused secret or extra would fail agent compilation at deploy time.
- **`config/config.yaml`** — the runtime parameters (`max_retry`,
  `timeout_s`, and the `retrieval` tuning block). The platform registry loads
  this file and passes it to the graph constructor as `config=`; the
  standalone server (`src/api/server.py`) does the same, so the declared
  values are live in every deployment shape.

### Runtime config forwarding (`_parent_config`)

`TradingGovernanceWorkflowGraphNode._parent_config()` loads
`config/config.yaml` and forwards the `retrieval` block under
`config["configurable"]` (never `{}`):

```
{"configurable": {"retrieval": {top_k, score_threshold, kb_path}}}
```

`get_subgraph()` passes this into `DomainWorkflowGraph(config=...)`; the inner
graph republishes the `retrieval` block into the inner initial state as the
JSON-string field `retrieval_config` (via `_extra_initial_state()`), so the
declared `top_k` / `score_threshold` are live at runtime. `RetrieveNode` and
`RerankFilterNode` read `top_k` / `score_threshold` exclusively from State
(`retrieval_config` — no `config` parameter reaches `execute()`), falling
back to safe module defaults that mirror the declared values when the field
is absent (e.g. a bare unit-test state). The end-to-end chain (file →
forwarding → inner state → node behaviour) is pinned by
`tests/unit/test_config_forwarding.py`.

v1 declares no LLM settings: no node consumes them (see the v1 Implementation
Note), and this template does not declare configuration without a consumer.
The v2 synthesis upgrade introduces its LLM settings together with the node
that reads them.

## Caller Metadata Channel

`/invoke` accepts an optional `input_context` object (size-capped at the
adapter, 256 KB serialized). The ONLY field this template consumes is
`channel` — a short transport label recorded in `enriched_context` — and it is
locked to an inert identifier (`[a-z0-9_]{1,32}`); a present-but-invalid
`channel` refuses the request with a field-naming error (the value is never
echoed). All other `input_context` keys are ignored entirely: never parsed,
rendered, or persisted by this template's nodes.

### State Definition

| Field | Type | Purpose | Layer |
|-------|------|---------|-------|
| `validated_input` | `NotRequired[str]` | identifier-stripped question payload | outer |
| `governance_answer` | `NotRequired[str]` | final answer, mapped from inner `formatted_answer` | outer |
| `search_query` | `NotRequired[str]` | normalised search query | inner |
| `query_filters` | `NotRequired[Optional[str]]` (JSON) | parsed structured params (`category`, `top_k`) | inner |
| `retrieval_config` | `NotRequired[Optional[str]]` (JSON) | forwarded declared `retrieval` block (config/config.yaml) | inner |
| `retrieved_documents` | `NotRequired[Optional[str]]` (JSON) | scored KB candidates (incl. `extra`) | inner |
| `ranked_documents` | `NotRequired[Optional[str]]` (JSON) | reranked + threshold-filtered passages | inner |
| `grounded_answer` | `NotRequired[str]` | rule-assembled grounded answer body | inner |
| `citations` | `NotRequired[Optional[str]]` (JSON) | `[{ref, id, title, source}]` | inner |
| `mrm_checklist` | `NotRequired[Optional[str]]` (JSON) | structured product: `[{item_id, requirement, guideline_ref, source_id}]` | both |
| `circuit_breaker` | `NotRequired[Optional[str]]` (JSON) | structured product: `{applicable, trigger_pct, cooling_off_minutes, market, legal_basis, source_id}` | both |
| `audit_trail` | `NotRequired[Optional[str]]` (JSON) | structured product: `[{step, detail, count, control, retention_years, legal_basis, source_id}]` | both |
| `fisc_mapping` | `NotRequired[Optional[str]]` (JSON) | structured product: `[{control_id, control_title, fisc_chapter, source_id}]` | both |
| `formatted_answer` | `NotRequired[str]` | final answer + sources + mandatory FIEA disclaimer | inner |
| `intake_notes` | `NotRequired[Optional[str]]` (JSON) | validation / parse notes (no PII, no echoed values) | inner |
| `trace_id` / `correlation_id` | `Optional[str]` | framework-managed tracing | both |

**State Constraints (mandatory):**
- Flat `TypedDict` only (primitives + JSON-serialisable types).
- Structured fields (dict / list[dict]) stored as JSON STRINGS via `to_json()` /
  `from_json()` — used consistently by every producer AND consumer (msgpack
  safety).
- Domain fields are `NotRequired[...]` (valid TypedDict before any node writes).
- `formatted_output` is NOT re-declared (backbone field stays framework-owned).
- No JWT, API keys, credentials, or raw personal identifiers in State.
- `InvocationContext` via `config["configurable"]` only (never in State).
- No Pydantic models / dataclasses / arbitrary Python objects.

## Security Design

### Trust gate / input validation

Every node declares `required_trust_level` (see tables above);
`PreProcessNode` (VERIFIED_EXTERNAL) rejects empty / non-string `user_input`
before the inner workflow runs. The standalone server elevates authenticated
Bearer callers to VERIFIED_EXTERNAL (`INVOKE_AUTH_TOKEN`); middleware-established
trust is never demoted.

### Identifier screen (PreProcessNode)

`_surface_strip_identifiers()` redacts IBAN, e-mail, and account/card-shaped
digit-group patterns from the payload before `validated_input` is written.
The grouped-digit pattern is year-guarded: a run in which EVERY group is a
plausible 4-digit year (fiscal-year rows such as "FY 2023 2024 2025 2026",
routine in governance questions) is left intact; any other grouped or
contiguous 10–19-digit run redacts. The framework `FunctionNode` default PII
scan additionally masks `user_input` / `validated_input` at every node
boundary. Both directions are pinned by tests: real financial sentences
survive byte-identical, identifier-shaped runs never reach the inner nodes.

### Injection screen (InputValidateNode, template-owned)

The node that parses the caller envelope enforces refusal ITSELF — never
relying on an upstream gate alone. The screen runs POST-PARSE (after
`json.loads()`, so `\u`-escaped payloads are already decoded) and walks every
key and string value of the parsed envelope — a hostile field NAME is as
actionable as a hostile value. Detection is anchored to high-precision forms:
chat-template control tokens as a token CLASS (any `<|...|>` marker,
`[INST]`/`[/INST]`, `<<SYS>>`/`<</SYS>>`, bare role tags) plus explicit
instruction-override and prompt/secret-exfiltration phrases. On a hit: status
ERROR, a class-naming error entry, and NOTHING carried forward — the matched
content is never echoed. Ordinary governance prose that shares vocabulary with
attacks (questions about system prompts, jailbreak testing, ignoring stale
data) is deliberately NOT refused; both directions are pinned by tests via
direct `execute()` calls.

### Caller numeric and string guards

- `top_k` (the only caller-controlled number) goes through a
  finite-and-bounded coercion: bools, non-numerics, non-finite and fractional
  floats are dropped with a field-naming note; integers are bounded into
  [1, 20]. Rejected values are never echoed into notes, logs, or audit
  payloads — only the field name and the reason.
- `category` and the caller-metadata `channel` are locked to inert
  identifiers (`[a-z0-9_]{1,32}`).
- The free-text question itself is identifier-stripped, injection-screened,
  whitespace-normalised, and length-capped (2,000 chars) before retrieval.

### Output gate (PostProcessNode + get_output)

`PostProcessNode` calls the module-level `_security_gate_output()` scan from
`execute()` — unconditional, no suppression flag. It scans NESTED strings
RECURSIVELY across BOTH the rendered `result` string AND all four structured
product fields (`mrm_checklist`, `circuit_breaker`, `audit_trail`,
`fisc_mapping`, after JSON-decoding) — a credential-shaped value buried
inside a structured entry cannot bypass a top-level-string-only scan. On a
violation, the output is replaced with a sanitised stub, ALL FOUR structured
fields are cleared, and `AgentStatus.ERROR` is returned.
`TradingGovernanceComplianceAgent.get_output()` applies a SECOND,
caller-facing gate on top: it never returns a raw `json.loads()` blob — each
structured entry is rebuilt from an explicit whitelist of scalar keys
(ids/numbers/urls/state), re-scanned recursively, and surfaced ONLY when
`status == SUCCESS` (fail-closed). No `_extra_security_gate_input` /
`_extra_security_gate_output` instance methods are defined on any node (the
framework auto-wraps such hooks — prohibited).

### Output precision

This template's rendered output contains NO monetary aggregates: the answer
body quotes seeded regulatory KB passages verbatim (with citations), and the
structured fields carry regulatory parameters (percent triggers, minute
durations, retention years, control identifiers) — none of them monetary
amounts, and the seeded KB contains no monetary values. A numeric rounding
grid is therefore NOT applied here: rewriting numbers inside verbatim-quoted
regulatory text or inside regulatory parameters would corrupt the quoted
guidance while protecting nothing. The output invariants this template DOES
enforce, completely: the mandatory non-suppressible FIEA advisory line on
every rendered answer, the recursive credential gate over the answer and all
structured fields, and SUCCESS-only whitelisted structured surfacing. If a
future revision renders monetary aggregates, an aggregate-only rounding
schema and its enforcement gate must be added together with that change.

### Audit logging

Every node's `execute()` emits exactly ONE domain-specific
`emit_trace_event("<node>_complete", {small non-PII payload}, state)` on its
success path (rejection paths emit `pre_process_rejected` /
`input_validate_rejected` with reason/field names only — never values).
Nodes do NOT emit `node_start` / `node_complete` / `node_error` —
`BaseNode.__call__()` emits those. Domain event names:

- `pre_process_complete` / `pre_process_rejected`
- `input_validate_complete` / `input_validate_rejected`
- `retrieve_complete`
- `rerank_filter_complete`
- `generate_answer_complete`
- `output_format_complete`
- `post_process_complete`

## Mandatory FIEA Disclaimer

Every answer carries the standing, NON-SUPPRESSIBLE FIEA (金融商品取引法)
advisory line — informational only, not legal/compliance/investment advice,
not a substitute for qualified counsel, verify against the primary
regulatory text. There is no config flag, state field, or caller parameter
that omits it. It is appended by `OutputFormatNode` as part of the domain
output contract — NOT injected by `post_process` (post_process only gates).

## Structured Product — `get_output()` override

The MRM checklist / circuit-breaker / audit-trail / FISC-mapping fields are
the deliverable, not incidental metadata.
`TradingGovernanceComplianceAgent.get_output()` (`src/graph/graph.py`)
OVERRIDES the framework default, EXTENDING it via `super().get_output(state)`
(never replacing it — `output` / `status` / `trace_id` / `correlation_id` /
`node_history` are preserved unchanged):

```python
def get_output(self, state) -> dict:
    base = super().get_output(state)
    if base.get("status") != AgentStatus.SUCCESS.value:
        return base
    structured = _build_structured_output(state)   # whitelist + recursive re-scan
    return {**base, **structured}
```

- Structured keys are surfaced **ONLY on SUCCESS** — any ERROR status
  (including a `PostProcessNode` output-gate block) returns the base envelope
  alone.
- `_build_structured_output()` never passes a parsed JSON blob through
  as-is: each entry is rebuilt from an explicit whitelist of scalar fields
  (`_MRM_ITEM_FIELDS`, `_CIRCUIT_BREAKER_FIELDS`, `_AUDIT_TRAIL_FIELDS`,
  `_FISC_MAPPING_FIELDS`), then every remaining string is recursively
  re-scanned for credential-shaped content; any residual hit drops **all
  four** structured keys for that response (fail-closed, not partial).

## v1 Implementation Note — LLM synthesis

v1 of this template is **deterministic end-to-end and network-free**:
retrieval is keyword scoring over the seeded/stubbed KB, and
`GenerateAnswerNode` assembles both the grounded answer AND the four
structured product fields rule-based from the ranked passages (lead sentence
+ cited passage excerpts for the answer; a deterministic fold of each
passage's `extra` payload for the structured fields). There is NO live LLM
call and no LLM client dependency in v1 — the manifest declares
`generation_mode: "deterministic"` with empty `requires` gates, no LLM
settings are declared (configuration is only declared together with a
consumer), and no `system_prompt` is read at runtime. The LLM synthesis
upgrade seam is documented in `config/prompts/answer_synthesis_prompt.md`: a
v2 `GenerateAnswerNode` swaps the rule-based `grounded_answer` assembly for
an LLM call over the same `ranked_documents` input and emits the same
`grounded_answer` / `citations` state contract — the v2 change also declares
its LLM settings and extras at that point. The four structured fields stay
rule-based even in v2 (they are compliance data extracted from the KB, not
prose to re-synthesise), so no other node changes.

## Composition Pattern

- **Pattern:** `GraphNode` (subgraph) in the outer `main` slot.
- **Composition target:** `DomainWorkflowGraph` (inner `BaseGraph`).
- **Error propagation strategy:** `propagate` (inner errors re-raised as `SubgraphError`).
- Inner domain nodes run at `TrustLevel.ANONYMOUS`; outer pre/post_process run
  at `TrustLevel.VERIFIED_EXTERNAL`.

## Import Isolation Confirmation
- [x] Template does not import the platform-internal SDK.
- [x] Import targets: `framework/` and `shared/` only.
- [x] The framework base classes are the only inheritance targets (no
      intermediate agent classes in any base position).

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| Framework base type | AgentBaseGraph | AutonomousBaseGraph | **AgentBaseGraph** | Fixed multi-step RAG workflow, no autonomous loop |
| Composition pattern | Standalone flat slots | GraphNode → inner BaseGraph | **GraphNode → inner BaseGraph** | 5-step domain workflow exceeds a single `main` node; nested keeps the outer backbone untouched |
| Answer synthesis | Rule-based assembly | LLM call | **Rule-based (v1)** | Deterministic assembly is self-contained and testable without a model backend; v2 swaps the LLM in at the documented seam |
| KB storage | External vector store | Seeded/stubbed JSON KB | **Seeded/stubbed JSON KB (v1)** | Self-contained, deterministic, network-free CI; the retrieval contract (`retrieved_documents` JSON) is store-agnostic for a later vector-store upgrade |
| Structured-product surfacing | Rendered text only | `get_output()` override with whitelist + recursive re-scan | **`get_output()` override** | MRM checklist / circuit-breaker / audit-trail / FISC-mapping are the product; a naive pass-through of parsed JSON would risk surfacing unvetted nested content |
| Numeric rounding grid | Aggregate rounding gate | No grid (invariants above) | **No grid** | The output renders no monetary aggregates — quoted regulatory text and regulatory parameters would be corrupted by a snap; see "Output precision" |
