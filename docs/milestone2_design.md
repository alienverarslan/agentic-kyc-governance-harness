# Milestone 2 — design note: `origin` + SYSTEM/T-family fail-closed error codes

Status: **design pinned, not yet implemented.** This file is committed together with the
implementation it describes. `DECISIONS.md` remains the short record of decisions already
in effect; this note is the forward-looking contract agreed before coding.

## Motivation

Both LLM boundaries in the graph — `ai_triage` and `synthesize` — can fail *operationally*:
the call can time out, the provider can error, the structured output can fail to parse.
Today neither is handled: the exception propagates and the whole screening run crashes.
That is not fail-closed; it is fail-*open-with-a-stack-trace*.

The obvious fix (emit an `X2` unexplainable finding) is **wrong**, and the reasoning
generalizes: a timeout is not *evidence about the company*. Modelling it as a domain
finding would pollute triage recall / false-positive metrics with operational noise,
conflating "the AI found something serious" with "the AI was unreachable". The same
critique applies to the `F2` code shipped in Faz 3 (a promoted rule raising at runtime):
it is an operational failure wearing a domain finding's clothes.

Operational failures therefore get their own origin and codes, keep their fail-closed
decision effect, and are reported **separately** from domain anomaly metrics.

Both LLM boundaries are in scope. Making `ai_triage` fail-closed while leaving
`synthesize` to crash the run would be arbitrary: it is the same contract applied to the
second call of the same boundary, not scope creep.

## 1. `origin` and `error_kind` — with a model-level invariant

Add to `Finding`:

```python
origin: Literal["deterministic", "ai_triage", "system"] = "deterministic"
error_kind: Optional[ErrorKind] = None
```

```python
ErrorKind = Literal[
    "timeout", "provider_error", "schema_invalid",
    "unexpected_exception", "rule_runtime_error",
]
```

**Backward-compatibility contract:** `origin` defaults to `"deterministic"`. Every existing
finding construction, fixture, and assertion keeps its semantics untouched. The P3 compound
suite asserts on finding **code sets**, never on origin, so it passes unchanged — a
deliberate forward-compatibility choice made during P3.

**Invariant, enforced by a Pydantic `model_validator`, not by convention:**

```
origin == "system"                        <=>  code in SYSTEM_CODES ({"T0", "T1"})
origin in {"deterministic", "ai_triage"}   =>  code not in SYSTEM_CODES
error_kind is not None                    <=>  origin == "system"
```

Without this, `origin="deterministic", code="T0"` is constructible and the claim "the
separation lives in `origin`" would be unenforced. Positive and negative tests required.

`error_kind` is a **bounded, machine-readable** enum so P5 can produce a failure breakdown
later without a breaking change to T-findings.

## 2. Decision: T-codes live in `TaxonomyCode`

`T0`/`T1` join the existing `TaxonomyCode` literal rather than forming a separate
`SystemErrorCode` union. A union would force every finding consumer, serializer, test,
code-set comparison and evaluator to handle two families, and would carry the same semantic
split in two places (code family *and* type family). The `Finding` contract is identical for
both; only the values differ. The `origin`/`error_kind` invariant is what makes the split
real.

| Code | Meaning | Examples |
|---|---|---|
| `T0` | LLM-call operational failure (either boundary) | timeout, invalid structured output, provider error |
| `T1` | Deterministic learned-rule runtime failure | a broken promoted rule, unexpected evaluator exception |

Which boundary failed is read from `check_name` (`ai_triage` / `synthesize`); which failure
occurred is read from `error_kind`. Sub-codes (`T0a`, `T0b`, …) are therefore unnecessary.

## 3. Where exceptions are caught — two distinct layers

**Architectural constraint found while planning:** the `anthropic` SDK is imported lazily
inside `AnthropicClient.__init__`, and `llm/factory.py` states that agent code "never names
a backend". So `graph.py` **cannot** catch `anthropic.APITimeoutError` directly — that would
make the offline path depend on the `anthropic` package and leak a provider name into the
agent layer. Translation happens at the **client boundary**, in the module that already
imports the SDK.

New `harness/llm/errors.py`:

```python
class LLMError(Exception):
    """Provider-agnostic LLM failure carrying a bounded, machine-readable kind.
    The original exception is preserved as __cause__ for logs only."""
```

### Layer separation (load-bearing)

The client normalizes **only known external / model-output failures**. It must NOT wrap
every `Exception` into an `LLMError`: a `KeyError` in our own client code is a harness
programming bug, and labelling it `provider_error` would hide it behind an
LLM-failure story. Unexpected exceptions propagate and are classified at the node boundary.

| Layer | Catches | Produces |
|---|---|---|
| `anthropic_client.py` | known SDK/provider errors and known response-parsing / structured-output errors | `LLMError(timeout \| provider_error \| schema_invalid)` |
| `ai_triage` / `synthesize` node boundary | `LLMError`; then any remaining `Exception` | `T0` with the carried `error_kind`, or `T0` / `unexpected_exception` |
| `learned_rules` node boundary | rule-evaluation runtime `Exception` | `T1` / `rule_runtime_error` |

`unexpected_exception` is therefore produced **only** at a node boundary, never by the
client.

### Exception matrix (concrete, from reading `AnthropicClient.complete_structured`)

| Where | Raised | Translated to |
|---|---|---|
| `messages.create` — request timed out | `anthropic.APITimeoutError` (subclasses `APIConnectionError`) | `LLMError("timeout")` |
| `messages.create` — network/connection failure | `anthropic.APIConnectionError` | `LLMError("provider_error")` |
| `messages.create` — 4xx/5xx incl. auth, rate limit, overloaded | `anthropic.APIStatusError` + subclasses | `LLMError("provider_error")` |
| any other SDK error | `anthropic.APIError` (base) | `LLMError("provider_error")` |
| `tool_use` block input fails the schema | `pydantic.ValidationError` | `LLMError("schema_invalid")` |
| text fallback is not JSON | `json.JSONDecodeError` (a `ValueError`) | `LLMError("schema_invalid")` |
| text fallback parses but fails the schema | `pydantic.ValidationError` | `LLMError("schema_invalid")` |
| anything else (e.g. a harness bug) | — | **not wrapped**; propagates to the node boundary |

Catch order matters: `APITimeoutError` before `APIConnectionError`, both before `APIError`.

### Policy at the node boundary

```
- Catch LLMError explicitly; map .kind -> error_kind on a T0 finding.
- Catch any remaining Exception as T0 / unexpected_exception (last-resort fail-closed).
- NEVER catch BaseException (KeyboardInterrupt / SystemExit must propagate).
- NEVER put raw exception text, provider messages, endpoints, response bodies, stack
  traces or prompt content into the user-facing Finding.detail.
```

`Finding.detail` is a fixed, bounded template naming the failing node and the `error_kind` —
nothing interpolated from the exception.

**Internal observability:** the original exception survives as `__cause__` and is recorded
via the standard `logging` module with bounded metadata only:
`{error_kind, exception_class, node, case_ref}`. No new trace-id system is introduced —
`case_ref` already is the run's correlation identifier.

> **This retro-fixes a real defect in the shipped Faz 3 code.** `learned_rules.py`
> currently renders `f"...failed to evaluate: {exc}..."` straight into the user-facing
> finding detail. The T1 migration removes that interpolation.

## 4. SYSTEM / T-family fail-closed matrix

| Code | Trigger | `error_kind` | Severity | Decision effect | Reported in |
|---|---|---|---|---|---|
| `T0` | LLM call failed at `ai_triage` or `synthesize` | `timeout` / `provider_error` / `schema_invalid` / `unexpected_exception` | `unexplainable` | escalate (fail-closed) | operational reliability |
| `T1` | Promoted-rule runtime exception (**migrated from `F2`**) | `rule_runtime_error` | `unexplainable` | escalate (fail-closed) | operational reliability |

Rules:
* **Fail-closed, never silent.** An operational failure is never swallowed and never lets a
  run continue as if the call had returned "no concerns". It escalates.
* **Never a domain signal.** T-codes are excluded from triage recall, false-positive rate,
  and every by-anomaly-type metric.
* **`F2` is retired.** Rationale for `DECISIONS.md`: *a rule-evaluation exception is an
  operational system failure, not domain evidence.* `F1` stays a domain code — a learned
  rule firing as intended is a genuine finding about the company.

## 5. `synthesize` failure: no fabricated proposal

When the synthesis call fails, the LLM produced **no proposal**. The audit record must not
imply otherwise. A sentinel `SynthesisProposal(proposed_decision="escalate")` is explicitly
rejected: it would read as "the LLM proposed escalate", which is false.

`SynthesisProposal` gains an explicit availability status (using `Literal`, consistent with
`Decision` / `Severity` / `TaxonomyCode`; this codebase uses no Enums):

```python
status: Literal["available", "unavailable"] = "available"
proposed_decision: Optional[Decision] = None
reasoning: str = ""
key_findings: list[str] = []
unavailable_reason: Optional[Literal["llm_call_failed"]] = None
```

On a synthesis failure the node produces:
* a `T0` finding (`origin="system"`, bounded `error_kind`, severity `unexplainable`);
* `SynthesisProposal(status="unavailable", proposed_decision=None, unavailable_reason="llm_call_failed")`.

The guardrail then reaches `escalate` from the T0 severity alone.

`GuardrailDecision` gains one field — not two. `proposal_available` and `finalization_mode`
would carry the same bit; the mode describes how the guardrail actually finalized, which is
what `GuardrailDecision` is about:

```python
finalization_mode: Literal["proposal_guarded", "fail_closed_no_proposal"] = "proposal_guarded"
```

`apply_guardrail` must special-case an unavailable proposal: `overridden=False` (there was
nothing to override) **together with** `finalization_mode="fail_closed_no_proposal"`, so
"not overridden" can never be silently misread as "the LLM already agreed".

### A model may not declare its own unavailability

Found while implementing: `SynthesisProposal` is handed to the model as a tool schema
(`complete_structured` calls `schema.model_json_schema()`), so adding `status` means a
*responding* model can emit `status="unavailable"`. That would let it manufacture a
"no proposal was available" audit record and thereby avoid being recorded as overridden.

It cannot affect the decision — findings alone drive that — but it corrupts the audit trail,
which is this project's whole thesis. **Unavailability is a fact the harness observes when a
call fails, never something a model that did answer may assert.** `synthesize` therefore
rejects a model-declared unavailable status as out-of-contract output and handles it
fail-closed (`T0` / `schema_invalid`), exactly like any other invalid structured response.

A cleaner long-term alternative is a separate wire model (the model emits a draft carrying
only `proposed_decision`/`reasoning`/`key_findings`; the harness wraps it), so the field is
literally inexpressible. That is deferred: it would touch every stub's schema dispatch, and
the guard above closes the hole within this milestone's narrow scope.

## 6. Scope: narrow

Milestone 2 closes a **safety gap**; it does not build a reporting layer.

**In scope:** `origin` + `error_kind` and their invariant; `LLMError` and the
client-boundary translation; T0/T1 fail-closed production at both LLM boundaries and the
learned-rule boundary; the `SynthesisProposal` availability contract and
`finalization_mode`; the `F2 -> T1` migration; exclusion of T-codes from domain recall /
FPR / by-anomaly-type metrics.

**Out of scope (P5):** the operational-reliability report format, Wilson confidence
intervals, corpus hashes, model/prompt/git provenance, dashboards, and the
`system-induced escalation rate`. Milestone 2 leaves only raw fields for P5 to render:

```python
system_failure_count: int
system_error_codes: list[str]
```

**Metric nuance.** T-codes are excluded from *domain* recall/FPR/by-type figures, but do not
vanish from the final-decision safety picture. `false_approval_rate` remains 0
**structurally**, not by assumption: T-codes are `unexplainable` and findings only ever add,
so a T-code can push a decision up but never produce an approval. A T-code landing on an
approve-truth case would depress *domain decision accuracy* — reporting that separately is a
P5 concern; the raw fields above make it possible. Offline stubs never emit T-codes, so the
existing corpora are unaffected.

## 7. Triage override contract (must remain invariant)

Existing, verified behaviour Milestone 2 must not regress:

1. Triage may only **add** findings; severity is clamped to `{explainable, unexplainable}`
   in `triage_findings()` — it can never emit `info` and never removes a finding.
2. The synthesis proposer **structurally cannot see the triage channel**: triage findings go
   to `state["findings"]`, not to `check_results`, which is what the proposer's payload is
   built from. On an otherwise-clean dossier the proposer proposes `approve` and the
   guardrail overrides upward — expected, not a defect (verified by `CMP-07`/`CMP-08` and
   `tests/test_triage.py`).
3. The guardrail takes the **maximum** severity across all findings and is the sole decision
   authority; it records every override with a reason.
4. Net property: triage, and any T-code, can raise a decision toward `request_more_info` /
   `escalate`, but can never lower one to `approve`.

## 8. Test plan (`tests/test_system_errors.py`, offline, deterministic)

1. **Synthesize timeout** — `T0` / `origin="system"` / `error_kind="timeout"`; final
   `escalate`; the run survives; proposal `status="unavailable"`.
2. **Synthesize invalid structured output** — `T0` / `schema_invalid`; final `escalate`; the
   user-facing detail contains no raw validation/provider text.
3. **Triage timeout** — `T0`; final `escalate`; pre-existing deterministic findings are
   retained, not dropped.
4. **Clean dossier + T0** — fail-closed escalation with no domain finding present.
5. **Existing domain A2 + synthesis failure** — `A2` and `T0` both retained; final `escalate`.
6. **T1 migration** — a learned-rule exception yields `T1` / `origin="system"` /
   `error_kind="rule_runtime_error"`, and **no `F2`** anywhere.
7. **No false proposer narrative** — on synthesis failure the test asserts the proposal is
   *unavailable*, NOT that it proposed `escalate`; and `finalization_mode ==
   "fail_closed_no_proposal"`.
8. **Origin/code invariant** — valid: `system`+`T0`/`T1`. Rejected by Pydantic:
   `system`+`A2`; `ai_triage`+`T0`; `deterministic`+`T1`; `error_kind` set with a non-system
   origin.
9. **Metric exclusion** — `T0`/`T1` stay out of domain recall/FPR/by-type figures while
   appearing in `system_failure_count` / `system_error_codes`; `false_approval_rate` stays 0.
10. **No-leak sweep** — the raw exception message appears in no `Finding.detail` across all
    of the above.
11. **Regression** — the full existing suite (seeds, 420-case generated corpus, compound
    suite) stays green with `origin` defaulted, and no T-code is produced by the offline
    stubs on the existing corpora.

## Language convention

Claims in this project are "validated under the evaluated corpora and tested scenarios",
not "proven". Every property above is scoped to the specific dossiers and stubs its tests
drive.
