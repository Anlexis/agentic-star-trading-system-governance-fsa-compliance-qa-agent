# Test Specification — FIN-C2-108

**Template ID:** FIN-C2-108
**Template Name:** TradingGovernanceComplianceAgent
**Category:** Cat 2 (nested RAG) — Trading Governance & Compliance

This document defines the test cases for the implementation (state, nodes,
inner/outer graphs, manifest, server). The test code lives in `tests/unit/` +
`tests/proof_of_boundary/`; this spec is the contract those tests implement.

## 1. Scope & Invocation Conventions

- Per-node unit tests for the 5 inner domain nodes + the 2 outer gate nodes.
- Manifest/config consistency (`config/agent.yaml` + `config/config.yaml` <->
  code), end-to-end runtime-config forwarding, and seeded-KB integrity across
  all four KB categories (`ai_mrm` / `algo_trading` / `fisc` / `audit`).
- Structured-product coverage: `GenerateAnswerNode`'s `mrm_checklist` /
  `circuit_breaker` / `audit_trail` / `fisc_mapping` assembly,
  `PostProcessNode`'s recursive output-gate scan over those fields, and
  `TradingGovernanceComplianceAgent.get_output()`'s SUCCESS-only whitelisted
  surfacing.
- Validation contract: the template-owned injection screen (post-parse, keys
  included), the finite-and-bounded numeric guard, and the inert-identifier
  locks on `category` / `channel`.
- Inner-graph (`DomainWorkflowGraph`) and outer-graph
  (`TradingGovernanceComplianceAgent`) composition / integration.
- Proof-of-Boundary (PoB): import isolation, State msgpack safety, invoke
  order (PB-6), HITL propagation (PB-7, conditional, auto-waived), server
  boot, and the end-to-end ASGI `/invoke` boundary (PB-8).

**Trust-gate invocation canon.** Per-node behavioural tests invoke the node
via `node(state)` — through `BaseNode.__call__`, which runs the trust gate ->
input mask -> `execute()` -> output gate — never a bare `node.execute(state)`.
The state builder sets `caller_trust_level` to
`TrustLevel.VERIFIED_EXTERNAL.value` for the two outer gate slots
(PreProcessNode / PostProcessNode — the manifest's declared caller level) and
`TrustLevel.ANONYMOUS.value` for the five inner domain nodes.
**Template-owned-guarantee carve-out:** the identifier-screen and
injection-screen suites ADDITIONALLY call `execute()` directly
(`TestTemplateOwnedScreenDirect`, `TestInjectionScreen`) — those screens are
the template's own guarantee and must hold even where no framework gate ran
in front; assertions there are behavioural (error status, nothing carried
forward), never a gate's wording.
**Config knobs:** every node's `execute(self, state)` takes no `config`
parameter at all — `RetrieveNode` / `RerankFilterNode` config knobs are
exercised by SEEDING the state field `retrieval_config`.

**Input-mask expectations.** The framework input gate masks
`user_input`/`validated_input`/`llm_response` (e-mail, phone/SSN/CC digit
groups, Title-Case name bigrams — regulatory acronym phrases like
`FSA Guidelines` / `Financial Instruments` count too) to `[MASKED]` before
`execute()` runs. Positive-path payloads are therefore lowercase, PII-free
regulatory phrasing; intentional-PII tests assert the raw identifier is gone
and the masked marker (`[MASKED]`, or `[REDACTED]` for the node's own
screen) is present. Domain fields (`grounded_answer`, `formatted_answer`,
`retrieved_documents`, `ranked_documents`, `mrm_checklist`,
`circuit_breaker`, `audit_trail`, `fisc_mapping`, ...) are not input-mask
scan targets.

**Audit muting.** `shared.*` is never sys.modules-stubbed (the framework
imports `shared.security` at load time). The domain audit emitter is muted
via an autouse fixture (`tests/unit/conftest.py`) patching
`src.nodes.<mod>.emit_trace_event`; audit assertion tests re-patch the same
attribute with a spy and assert on `call.args[1]` (the event payload).

## 2. Unit Test Cases

### 2.1 PreProcessNode (outer pre_process slot) — `test_pre_process_node.py`

| ID | Case | Input | Expected |
|----|------|-------|----------|
| PRE-01 | Valid query | lowercase governance question | `status=SUCCESS`, `validated_input` set, `enriched_context` carries channel/source |
| PRE-02 | Empty input | `""` / whitespace | `status=ERROR`, `error_log` non-empty, no `validated_input` |
| PRE-03 | Missing / non-string input | `user_input` absent; dict payload | `status=ERROR` |
| PRE-04..06 | Identifier screen | IBAN -> node screen; e-mail / 4-4-4 digit groups -> framework mask | raw identifier absent from `validated_input`; `[REDACTED]` (node screen) / `[MASKED]` (framework) present |
| PRE-08 | Domain audit | valid query | `pre_process_complete` emitted; payload (`call.args[1]`) carries `input_chars`; rejection audit names field/reason only |
| PRE-09 | Title-Case regulatory bigram masked | `"Financial Instruments"` embedded in raw input | raw bigram absent from `validated_input`; `[MASKED]` present |
| PRE-10 | Caller metadata `channel` | valid inert id accepted; free-text / non-string / oversized `channel` | invalid present `channel` -> `status=ERROR`, field named, value NEVER echoed; absent -> `"unknown"`; unconsumed context keys ignored |
| PRE-11 | Template-owned screen, both directions (direct `execute()`) | fiscal-year rows (`2023 2024 2025 2026`), bare years/percentages | survive byte-identical |
| PRE-12 | Template-owned screen, both directions (direct `execute()`) | card/account-shaped runs (spaced, hyphenated, contiguous, year+non-year mix), IBAN, e-mail | redacted without any framework gate in front |

### 2.2 InputValidateNode (inner node 1) — `test_input_validate_node.py`

| ID | Case | Input | Expected |
|----|------|-------|----------|
| VAL-01 | Plain text | free-text question | whole string becomes `search_query`; filters `{category: None, top_k: None}` |
| VAL-02 | Whitespace | ragged spacing/newlines | collapsed to single spaces |
| VAL-03 | JSON envelope | `{"query","category","top_k"}` | all three parsed; `question` alias accepted; category lower-cased/stripped |
| VAL-04 | Malformed JSON | `{`-prefixed non-JSON | treated as plain-text query + parse note |
| VAL-05 | top_k out of range | 99 / -5 | clamped to 20 / 1 + field-naming note (value not echoed) |
| VAL-06 | top_k non-numeric | `"many"` | dropped (None) + note |
| VAL-08 | Oversize query | > 2000 chars | truncated to 2000 + note |
| VAL-09 | Empty request | `""` | `search_query=""` + "empty request" note (non-fatal) |
| VAL-10 | **Injection screen** (direct `execute()`) | control tokens (`<\|...\|>`, `[INST]`, `<<SYS>>`, role tags), override/exfiltration phrases, token inside a JSON value, `\u`-escaped token (post-parse), hostile field NAME | `status=ERROR`; NOTHING carried forward (no `search_query`/`query_filters`); matched content never echoed. Real governance sentences sharing the vocabulary (system-prompt governance, jailbreak testing, "ignore stale market data") are NOT refused |
| VAL-11 | **Numeric matrix** (per field) | `"NaN"` / `"Infinity"` / `"-Infinity"` / `True` / `False` / `[]` / `{}` / `3.7` / raw JSON `NaN`/`Infinity` literals / `10**60` | non-finite & non-integral dropped (None) with field-naming note; over-magnitude clamped into bounds; integral string/float accepted |
| VAL-12 | Category guard | free text / oversized / non-string category | filter dropped with field-naming note (value not echoed) |
| — | State safety | any | `query_filters` is a JSON string, never a bare dict |

### 2.3 RetrieveNode (inner node 2) — `test_retrieve_node.py`

Query scores verified offline against the actual tokenizer + per-field-weight
scorer in `src/nodes/retrieve_node.py`, run against the real seeded
`config/kb/trading_governance_kb.json` (title 1.0 > tags 0.8 > content 0.5,
averaged over non-stopword query tokens) — not hand-guessed.

| ID | Case | Input | Expected |
|----|------|-------|----------|
| RET-01 | Happy path | ai_mrm query ("independent model validation before deploying a trading agent") | top-1 candidate is `kb-001` (score 1.0000, margin over `kb-016` at 0.5000) |
| RET-02 | Ordering | same query | scores strictly sorted desc; all > 0 |
| RET-03 | Entry shape | any hit | keys `{id,title,category,source,score,excerpt,extra}`; excerpt <= 400 chars |
| RET-04 | `extra` payload carried verbatim | ai_mrm query | `kb-001.extra.mrm_items` present with 2 items (feeds `GenerateAnswerNode`'s `mrm_checklist`) |
| RET-05 | Category filter | `query_filters.category="fisc"` | only fisc entries; top-1 `kb-010` (score 1.0000, margin over `kb-009` at 0.2000) |
| RET-06 | Empty query | `""` | no candidates |
| RET-07 | Out-of-domain query | "quantum telepathy sandwich recipes" | no candidates (zero KB overlap) |
| RET-08 | State config override | `retrieval_config.kb_path` bogus (state-seeded, no config arg) | `[]` + "not readable" note |
| RET-09 | Module defaults | no `retrieval_config` key at all (bare unit-test state) | resolves the real seeded KB path; non-empty results |
| — | Notes accumulation | prior `intake_notes` | appended, never clobbered |

### 2.4 RerankFilterNode (inner node 3) — `test_rerank_filter_node.py`

Candidates are synthetic dicts (not real KB retrieval) — fully deterministic,
isolated from RetrieveNode / the seeded KB content.

| ID | Case | Input | Expected |
|----|------|-------|----------|
| RRF-01 | Relevance floor | scores 0.9 / 0.1 | 0.1 dropped (default 0.25 floor) |
| RRF-02 | State score_threshold override | `retrieval_config.score_threshold=0.5` (state-seeded) | 0.3 dropped |
| RRF-03 | State top_k override | `retrieval_config.top_k=1` (state-seeded) | one survivor, highest score |
| RRF-04 | Category boost | matching category | +0.1, re-ranked ahead |
| RRF-05 | Boost cap | 0.95 + boost | capped at 1.0 |
| RRF-06 | Caller top_k | stricter (1) wins; looser (10) does not widen; bools never count as numbers | enforced |
| RRF-07 | Garbage entries | non-dict / uncoercible score | skipped / coerced to 0.0 and dropped |
| RRF-08 | Tie-break | equal scores | deterministic id-ascending order |
| — | `extra` pass-through | any survivor | unchanged (consumed only by `GenerateAnswerNode`) |

### 2.5 GenerateAnswerNode (inner node 4 — canonical structured-product node) — `test_generate_answer_node.py`

| ID | Case | Input | Expected |
|----|------|-------|----------|
| GEN-01 | Citation markers | 2 ranked passages | `[1]`/`[2]` markers with titles |
| GEN-02 | Lead sentence | query present | query quoted in the lead |
| GEN-03 | Citations list | ranked passages | refs 1..n mirror ranked order; id/title/source carried |
| GEN-04 | Groundedness | single passage | answer body traces to ranked passages only |
| GEN-05 | `mrm_checklist` fold | single `ai_mrm` doc, 2 `extra.mrm_items` | both items emitted, each tagged `source_id` |
| GEN-06 | `mrm_checklist` non-contribution | non-`ai_mrm` doc | `mrm_checklist == []` |
| GEN-07 | `circuit_breaker` first-match rule | 2 `algo_trading` docs, both carry `extra.circuit_breaker` | ONLY the top-ranked (first) doc's block is used |
| GEN-08 | `circuit_breaker` default | no `algo_trading` doc in `ranked_documents` | `{"applicable": False, ...all None}` |
| GEN-09 | `fisc_mapping` fold-all | 2 `fisc` docs | BOTH contribute an entry (unlike circuit_breaker's first-only rule) |
| GEN-10 | `audit_trail` baseline + match | 1 `audit` doc + N pre-rerank candidates | `[retrieve(count=N), rerank_filter(count=1), audit_requirement_match(...), generate_answer(count=1)]` |
| GEN-11 | `audit_trail` baseline-only | no `audit`-category doc | exactly the 2 pipeline steps + `generate_answer` (3 entries); always non-empty on SUCCESS |
| GEN-12 | No coverage | empty/missing `ranked_documents` | escalation answer; `citations=[]`; all 4 structured fields empty/default |

### 2.6 OutputFormatNode (inner node 5, terminal) — `test_output_format_node.py`

| ID | Case | Input | Expected |
|----|------|-------|----------|
| FMT-01 | Full compose | body + citations | header (`# Trading Governance & Compliance Answer`) + body + `## Sources` rows + mandatory FIEA disclaimer; `status=SUCCESS` |
| FMT-02 | Blank source | citation without source | no `()` suffix |
| FMT-03 | Disclaimer | every input (incl. empty body) | disclaimer rides with every answer, non-suppressible |
| FMT-04 | No citations | empty list | explicit "- none (...)" sources line |
| FMT-05 | Missing body | no `grounded_answer` | fallback text; `status=SUCCESS` |

### 2.7 PostProcessNode (outer post_process slot; recursive output gate) — `test_post_process_node.py`

| ID | Case | Input (`result` / structured fields) | Expected |
|----|------|------------------|----------|
| POST-01 | Clean output | normal governance answer | `formatted_output=result`, `status=SUCCESS` |
| POST-02 | Empty result | `""` | forwarded as-is, `status=SUCCESS` (non-fatal) |
| POST-03 | Clean structured fields | valid `mrm_checklist` JSON | passes through untouched (no key added to the delta) |
| POST-04..07 | Credential leak (top-level `result`) | `sk-` API key / `password=` assignment / JWT (built at runtime) / Bearer token | `formatted_output` + `result` replaced with the sanitised stub, `status=ERROR`, raw secret absent; all 4 structured fields cleared (fail-closed) |
| POST-08 | **Recursive scan — list-nested** | credential buried inside `mrm_checklist[1].requirement` (2 levels deep); top-level `result` clean | `status=ERROR`; ALL FOUR structured fields cleared, not just the offending one |
| POST-09 | **Recursive scan — dict-nested** | credential buried inside `circuit_breaker.legal_basis` (1 level deep); top-level `result` clean | `status=ERROR`; secret absent from the entire result |
| POST-10 | **Regression guard** | credential buried inside `audit_trail[1].control`; `result` squeaky clean | proves a top-level-only scan would have PASSED this case — the recursive scan must not |

POST-08..10 pin the exact defect class
`PostProcessNode._security_gate_output()` / `_iter_nested_strings()` was
built to close: a scanner that only reads the top-level rendered string.

### 2.8 Manifest / config consistency — `test_config_manifest.py`

| ID | Case | Expected |
|----|------|----------|
| CFG-01 | Template id | `id` = `FIN-C2-108` (root-level key) |
| CFG-02 | Class-name contract | manifest `class` is the single dotted path `src.graph.graph.TradingGovernanceComplianceAgent`; `name` == the graph class name; no `agent:` nesting block |
| CFG-03 | Classification | Cat 2 / FIN / RAGAgent / namespace `fin` / enabled |
| CFG-04 | Trust level | manifest `VERIFIED_EXTERNAL` == PreProcessNode & PostProcessNode `required_trust_level` |
| CFG-05 | max_retry | `config/config.yaml` int, `0 <= v < 10` (framework ceiling); hitl not enabled (PB-7 waiver contract) |
| CFG-06 | Retrieval block | `config/config.yaml` `top_k`/`score_threshold` mirror node module defaults; `kb_path` exists |
| CFG-07 | `_parent_config()` | forwards the declared `retrieval` block from `config/config.yaml`; never `{}` |
| — | Compile-time gates | `generation_mode: deterministic`; `requires.secrets == []`; `requires.extras == []` (deterministic template — an unused declaration would fail compile at deploy) |
| — | KB integrity | JSON list >= 5 entries; unique ids; required keys (incl. `extra`) per entry; all 4 categories (`ai_mrm`/`algo_trading`/`fisc`/`audit`) present |

### 2.9 Runtime-config forwarding (end-to-end) — `test_config_forwarding.py`

| ID | Case | Expected |
|----|------|----------|
| CFG-10 | Declared `top_k` reaches the rerank cut | a temp config declaring `top_k: 1` bounds the rendered citations to `[1]` only (baseline control: the same query cites `[2]` under the shipped config) |
| CFG-11 | Declared `score_threshold` reaches the filter | `score_threshold: 1.0` filters every passage -> the explicit no-coverage answer |
| CFG-12 | `_parent_config()` reads the declared file | a temp config's values appear verbatim under `configurable.retrieval` |

## 3. Integration / Composition

### 3.1 Inner graph — `test_domain_workflow_graph.py`

| ID | Case | Expected |
|----|------|----------|
| INT-01 | Composition | inherits `BaseGraph`; registers exactly the 5 domain nodes; no initialize/finalize |
| INT-02 | Config forwarding | `_extra_initial_state()` republishes the retrieval block as the JSON-string `retrieval_config` |
| INT-03 | Output shape | `get_output()` emits `formatted_answer`/`citations`/the 4 structured fields/`status`/`error_log`/`intake_notes`/`trace_id`/`correlation_id`/`node_history` (12 keys, the merge contract); `route()` -> END on error |
| INT-04 | Inner e2e | full inner `invoke()` -> SUCCESS; formatted answer + disclaimer + `kb-001` top citation; `mrm_checklist[0].source_id == "kb-001"`; inner `node_history` = the 5 domain nodes in linear order; out-of-domain query still terminates SUCCESS with the no-coverage answer |

### 3.2 Outer graph + e2e — `test_graph_composition.py`

| ID | Case | Expected |
|----|------|----------|
| INT-05 | Outer composition | inherits `AgentBaseGraph` (direct framework inheritance); `Graph` alias; `add_edges()` NOT overridden |
| INT-06 | Backbone slots | compile() fills all 5; pre/main/post are `PreProcessNode` / `TradingGovernanceWorkflowGraphNode` / `PostProcessNode` |
| INT-07 | `get_subgraph()` | returns `DomainWorkflowGraph` carrying the forwarded retrieval config |
| INT-08 | `extract_input()` | prefers `validated_input`, falls back to `user_input` |
| INT-09 | `merge_output()` | inner `formatted_answer` -> outer `governance_answer` AND `result`; `citations`/4 structured fields/`status` mapped 1:1; changed keys only (8-key delta) |
| INT-10 | Config fallback | `_parent_config()` never `{}` even with an unreadable `config/config.yaml` |
| INT-11 | e2e happy path | VERIFIED_EXTERNAL invoke -> SUCCESS; `output` = gated formatted answer; PreProcessNode/main/PostProcessNode all traversed; out-of-domain query still SUCCESS |
| INT-12 | e2e trust denial | ANONYMOUS invoke -> ERROR; empty `output`; PostProcessNode NOT traversed; `node_history[:2] == [InitializeNode, PreProcessNode]` |
| INT-13 | **Structured surfacing (SUCCESS)** | `get_output()` returns already-parsed Python `list`/`dict` (not JSON strings) for all 4 structured fields; `mrm_checklist` non-empty with `source_id == "kb-001"` |
| INT-14 | **Structured surfacing (ERROR, fail-closed)** | on a trust denial, all 4 structured keys are ABSENT from the response — never partial/stale |
| — | State-safety helpers | `to_json`/`from_json` round-trip; None/malformed handling |

## 4. Proof-of-Boundary

| ID | Case | Expected |
|----|------|----------|
| PB-IMPORT | `test_import_isolation.py` | no platform-internal SDK import anywhere under `src/` |
| PB-STATE | `test_state_safety.py` | `State` has no credential-named fields and no `BaseModel` / `InvocationContext` annotations |
| PB-6 | `test_pb_invoke_order.py` | full `Graph().invoke()` with `InvocationContext(caller_trust_level=VERIFIED_EXTERNAL)` (never `for_internal()`) over `_VALID_PAYLOAD` — loaded directly from `deploy/invoke_payload.json` at import time (byte-equal BY CONSTRUCTION, not a hand-copied literal, given the non-ASCII 金商法 content) -> SUCCESS with outer `node_history` exactly `[InitializeNode, PreProcessNode, TradingGovernanceWorkflowGraphNode, PostProcessNode, FinalizeNode]` |
| PB-7 | `test_pb7_hitl_interrupt_propagation.py` | **Auto-waived — non-HITL** (no graph class declares `propagate_hitl=True`; no interrupt checkpoint); canonical conditional skip-stub retained |
| PB-BOOT | `test_server_boot.py` | `import src.api.server` does not raise; module-level agent is `TradingGovernanceComplianceAgent`, compiled; fresh ctor->`compile()` fills the 5 backbone slots; `/health` reports the agent |
| PB-8 | `test_pb_invoke_endpoint.py` | end-to-end through the real ASGI `/invoke` (Bearer auth): runtime config reaches the compiled graph; a governance question yields a cited, KB-grounded, query-specific answer with structured fields; caller `category`/`top_k` demonstrably change the output; injection (raw AND `\u`-escaped) refused with nothing published; invalid `channel` refused without echo; oversized `input_context` -> 413; wrong/missing token -> generic 401; every SUCCESS carries the advisory disclaimer; no credential-shaped content in answer or structured fields; a pasted account number never reaches the response |

> **Pre-review gate checklist:** PB-IMPORT, PB-STATE, PB-6, PB-BOOT and PB-8
> are mandatory. PB-7 applies only to HITL-enabled templates — this template
> is non-HITL, so PB-7 is **Auto-waived — non-HITL** and its skip must not
> block the gate.

## 5. Framework Compliance Tests (Mandatory, TC-01..11)

| TC-ID | Test | Expected Result | Result |
|-------|------|----------------|--------|
| TC-01 | State contract: flat TypedDict | Type check pass, no Pydantic/dataclass | Pass — `src/schemas/state.py` |
| TC-02 | Invalid input refused | Error state returned | Pass — `PreProcessNode` empty-input path |
| TC-03 | No JWT/Credential in State | credential scan: 0 violations | Pass (CI gate) |
| TC-04 | InvocationContext via configurable only | Direct access raises error | Pass — no State field carries it |
| TC-05 | No duplicate lifecycle events in `execute()` | `node_start`/`node_complete`/`node_error` absent from `execute()` body | Pass — nodes only call domain `emit_trace_event` |
| TC-06 | `_security_gate_input()` not overridden | `TypeError` at class definition | Pass — `test_framework_compliance_tc06_tc07.py` |
| TC-07 | `_security_gate_output()` not overridden | `TypeError` at class definition | Pass — `test_framework_compliance_tc06_tc07.py` |
| TC-08 | `required_trust_level` enforced | Insufficient trust -> refused | Pass — `test_trust_gate.py` |
| TC-09 | Input-gate domain hook non-trivial | n/a — no `_extra_security_gate_input` on any node (module-level gates instead) | N/A by design |
| TC-10 | Output-gate domain hook non-trivial | n/a — module-level `_security_gate_output()` in `PostProcessNode` (never an instance hook) | N/A by design |
| TC-11 | >=1 domain `emit_trace_event()` per `execute()` | Domain event emitted on every invocation path | Pass — 7 nodes, success + rejection events |

## 6. Business Logic Tests

Covered by section 2.5 (`GenerateAnswerNode` GEN-05..12) and section 2.8
(seeded-KB integrity) — the domain business logic of this template IS the
structured-product assembly, so it is specified there rather than duplicated
in a separate BL-xx table.

## 7. Test Execution Summary

- Execution date: 2026-08-27
- Runner: real SDK wheel (`agenticstar-agentcore==1.0.1`), full-tree
  `python -m pytest tests/` (`tests/unit/` + `tests/proof_of_boundary/`)
- Total tests: 217 (216 passed, 1 skipped)
- Pass: 216 / Fail: 0 / Skip: 1 (PB-7 conditional skip-stub — auto-waived, non-HITL)
- Determinism: no LLM, no network; retrieval + answer assembly are rule-based
