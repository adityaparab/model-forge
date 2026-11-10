# SP4: Challenger Agent, Remediation Loop and Eval Harness

**Status:** approved in brainstorming (2026-11-10), awaiting written review
**Date:** 2026-11-10
**Parent:** `docs/superpowers/specs/2026-11-09-modelforge-architecture-design.md` sections 5.2, 8.3, 8.4, 10.1, 14, 15, 17 row 4, and section 20 (SP3 as built); `docs/superpowers/specs/2026-11-09-sp3-gateway-knowledge-agents-design.md` section 13; `docs/superpowers/notes/2026-11-09-sp3-handoff.md` ("Recommendations for SP4", "Deferred minors", "Rulings"). Brainstorm record: the shared notes folder, `sp4-brainstorm-2026-11-10.md`.
**Branch:** `sp4-challenger-evals`, cut from `main` at `5519f5d`.

---

## 1. Purpose and done criteria

SP4 replaces the last two deterministic stand-ins with gateway agents, closes the review loop the umbrella spec describes, and adds the eval harness that measures every profile the same way.

Done when, at the branch head:

1. `make lint` is clean (ruff, ruff format, pyright, import-linter with the contracts of section 2) and `MODELFORGE_REQUIRE_DB=1 make test` passes the full suite, slow tests included.
2. `MODELFORGE_PROFILE=fake uv run modelforge run examples/briefs/polish-pd.yaml` through the three gates produces challenger findings (pre-check and review), remediation responses, regenerated sections, a re-review that closes what was remediated, and a sealed manifest whose `findings` and `responses` refs carry the whole history; `modelforge replay` reproduces every deterministic artifact id; `modelforge replay --rerun-llm` regenerates `scope`, `document`, `challenge` and `remediate` and lists the LLM-derived refs as regenerated; `modelforge verify` exits 0.
3. `uv run modelforge-evals run --profile fake --baseline evals/baselines/fake.json` runs offline, writes an `eval_report` artifact and a report file, and exits 0 against the committed baseline; CI runs it.
4. The live layers ran once under `default` (`claude-opus-5-5`) and once under `ci-cheap` (`claude-sonnet-5-5`) with the user's authorisation of about 6 USD; the README table and `evals/baselines/{default,ci-cheap}.json` record the measurements.
5. The README gains "Challenger review", "Remediation loop" and "Evals" sections; `docs/superpowers/notes/2026-11-10-sp4-handoff.md` records verification, deliverables, SP5 recommendations, deferred minors and rulings in the SP3 note's shape; the umbrella spec gains section 21 (SP4 as-built amendments).

Out of scope (SP5 and later): the API, the console, OpenTelemetry, the LLM judge of document quality (section 15), a hand-labelled finding set, the local-ollama parity row for SP4's features, a dense retrieval channel.

## 2. Packages and dependency rules

- New package `packages/evals` (`modelforge_evals`, console script `modelforge-evals`), a workspace member added to the root `pyproject.toml` dependencies, `[tool.uv.sources]`, pytest `testpaths` and the Dockerfile's dependency layer (its `pyproject.toml` must be copied for `uv sync --frozen`; the `evals/` data directory is not copied into the image).
- Import-linter: the layers contract becomes `modelforge_evals > modelforge_orchestrator > (modelforge_mcp | modelforge_gateway) > modelforge_knowledge > modelforge_lineage > modelforge_core`; contracts 6 (no provider SDKs or HTTP clients) and 7 (only `modelforge_gateway.providers` imports the LangChain integrations) add `modelforge_evals` to their source lists; `modelforge_evals` is also LangGraph-free apart from what it reaches through `modelforge_orchestrator` (a new forbidden contract: `modelforge_evals` must not import `langchain_core`, `langgraph` or `mcp` directly). Eight contracts after SP4.
- Everything else as SP3: no pickle, explicit seeds, no wall-clock values inside artifacts (durations are allowed; timestamps live in lineage rows), MCP servers untouched (no new tool, no version bump), prompts immutable once shipped, SR 26-2 wording guarded by the existing test over `knowledge-base/` and `prompts/` (which now covers the new prompt directories automatically).

## 3. Challenger agent (`nodes/challenge.py`, purpose `challenge`)

### 3.1 Context and isolation

The challenger's LLM context is built by one function, `challenger_context(state, deps) -> ChallengerContext`, whose only sources are lineage artifacts read by id:

- the rendered document, `refs["document_md"]` (Markdown bytes);
- the validation report, `refs["validation"]`, through the existing `validation_report` excerpt projection;
- the modeling plan, read from `runs.plan_ref` through the store (never `state.plan`);
- the knowledge passages retrieved for its own queries (section 3.3) and the `knowledge_index` artifact;
- for the pre-checks only (never sent to the model): the `completeness` report and, by id, the artifacts the document's markers cite.

It never reads the brief body, `scope_notes`, `document_sections` (drafts, citations, grounding reports), any `llm_response` artifact, the column dictionary, or the excerpt of any developer artifact other than the validation report. The redaction scan list for its calls (the brief's `restricted_terms` plus the dataset's original column names, as in the document node) is a filter the gateway applies to outbound text; it is never prompt content.

The pre-checks (3.2) may read any artifact the document cites, by id, to verify a marker; that is deterministic verification over sealed artifacts, not LLM context.

The sentinel test (`test_challenger_isolation.py`) plants distinct sentinel strings in the brief's `constraints`, in `scope_notes.rationale` and `open_questions`, in a section draft's grounding report and `body_md` of a superseded `document_sections` artifact, in a stored `llm_response` text and in a dictionary column description; runs `challenge` with a recording gateway (a `chat_model_factory` that captures every message); and asserts that no sentinel appears in any system or user message of any `challenge` call. A second assertion lists the artifact ids the node read (through a recording store) and checks they are all of kinds `document_md`, `validation_report`, `modeling_plan`, `knowledge_index`, `completeness` or kinds cited by a marker in the document.

The challenger has its own routing purpose, so a profile may route it to a different model from the developer agents (umbrella threat model: "optional separate model"); the shipped profiles route it to the same model.

### 3.2 Deterministic pre-checks

Run before any LLM call, on every challenge round, over the current rendered document. Each failing check is a `Finding` with `source="precheck"` and a stable `check` key, so a later round can close it when the check no longer fires. Sections are recovered from the rendered Markdown by the template headings (`## <n>. <title>`, as `check_completeness` does).

| # | Check | `check` key | Category | Severity | Evidence |
|---|---|---|---|---|---|
| 1 | Every required template section is present and is not the author-attention banner (from the `completeness` report) | `missing_section:<id>` | `documentation` | `critical` | `<id>` |
| 2 | No section carries the `REQUIRES AUTHOR ATTENTION` banner | `bannered_section:<id>` | `documentation` | `high` | `<id>` |
| 3 | Every validation test with status `fail` or `warn` is discussed: the document contains an `[A:<validation_id>#tests[<test_id>]...]` marker, or names the test (`ValidationTest.name` or `test_id`, case-insensitive) | `undiscussed_test:<test_id>` | `performance` | `high` for `fail`, `medium` for `warn` | `performance`, `[A:validation#tests[<test_id>].status]` |
| 4 | Out-of-time and sensitivity: a test whose status is `not_applicable` (`disc.*.oot`, `sens.*`) is named in the document (`name` or `test_id`) in a sentence that also says `not applicable` or `not available`; an applicable one is cited as in check 3 | `unaddressed_na:<test_id>` | `performance` | `high` | `performance`, `[A:validation#tests[<test_id>].message]` |
| 5 | Independent marker re-verification: for every section, `grounding.check_section` over the rendered body, resolving `[A:]` markers against the stored artifact by id through `excerpts.excerpt(kind, ...)` (the kind from the `artifacts` table) and `[K:]` markers against the set of citation ids in the `knowledge_index` artifact; any ungrounded number, unresolved marker, uncited regulatory sentence or unknown passage fails | `reverify:<id>` | `documentation` | `high` | `<id>` and the failing items quoted in the description |
| 6 | Regulatory citation consistency: a sentence naming a regulation (`REGULATION_NAME` of `grounding.py`) and citing `[K:<id>]` cites a chunk whose `doc_id` is the regulation's (`SR 11-7 -> sr11-7`, `SR 26-2 -> sr26-2`, `OCC -> occ-2026-13`, `CECL`/`ASC 326 -> cecl-asc-326`, `IFRS 9 -> ifrs9-ecl`, `Basel -> basel-irb`) or whose text names that regulation | `miscited_regulation:<id>:<n>` (section id, sentence ordinal) | `documentation` | `medium` | `<id>`, `[K:<citation_id>]` |

Check 5 is SP3's grounding check run again from the reviewer's side over sealed artifacts, so a swapped number or a document edited after writing is caught deterministically.

### 3.3 Review (challenge round 1 only)

1. **Query planning**, `challenge/plan_queries.v1`: the template section titles, the plan's `regulatory_scope`, the validation statuses per test, and the pre-check findings. Output `ChallengeQueryPlan{queries: list[str]}` (0 to 5). Each query runs `search_regulation(k=3)`; hits are deduplicated by citation id in score order.
2. **Review**, `challenge/review.v1`: the rendered document, the validation excerpt, the plan, the passages and the pre-check findings (so the model does not repeat them). Output `ReviewDraft{findings: list[FindingDraft]}` with `FindingDraft{severity, category, title, description, evidence: list[str], required_action}`.

Evidence grammar (shared with remediation): a template section id (`performance`), an artifact marker `[A:<alias>#<path>]` where the challenger may use the aliases `validation` and `plan` only, or a passage marker `[K:<citation_id>]`. A `validate` hook rejects an unknown section id, an alias outside the allowed set, a path that does not resolve against the alias's excerpt, or a passage not retrieved in this round; the gateway retries with the message, then parks (`schema`). Stored evidence has aliases expanded to artifact ids (as document citations do).

Severity meaning (recorded in the system prompt and the README): `critical`, a required section or validation result is missing and the document cannot be relied on; `high`, a material weakness in data, method, performance or an ungrounded statement that must be fixed before use; `medium`, a weakness to fix or to accept as a sign-off condition; `low`, editorial.

### 3.4 Re-review (challenge rounds 2 and later)

Pre-checks run again over the re-rendered document. A pre-check finding whose check no longer fires is `closed` (verdict "check passes after remediation", `closed_round` = this round); one that still fires keeps its status (`accepted` becomes `open` again, `disputed` stays `disputed`) with the verdict noting it; a check that newly fires on a changed section raises a new pre-check finding. One LLM call, `challenge/re_review.v1`, receives the non-closed review findings with their responses, the ids of the sections regenerated in the last remediation round with their new bodies, and the plan and validation excerpts; it returns `ReReviewDraft{verdicts: list[Verdict]}` with `Verdict{finding_id, status: closed|open|disputed, note}`, one per finding it was given (the hook checks coverage and ids). The re-review raises no new LLM findings.

### 3.5 Findings model and storage

`Finding` (orchestrator `schemas.py`) gains `source: Literal["precheck", "review"] = "review"`, `check: str | None = None`, `verdict: str | None = None`, `closed_round: int | None = None`; `status` keeps `open|accepted|disputed|closed`. Ids are `F-<n>`, sequential across rounds; `round` is the challenge round that raised the finding. The `findings` artifact (`FindingsList{round, findings}`) is cumulative: every finding with its current status, so the manifest's `findings` ref is the whole history. Summary: `{n_findings, max_severity, by_severity, by_status, by_source, round}`. Rows: `insert_findings` for new findings; `set_response`/`set_verdict` updates; migration `0004_sp4.sql` adds `source text not null default 'review'`, `check text null`, `verdict text null`, `closed_round int null`.

### 3.6 Routing

`challenge` returns `Command(goto="remediate")` when some finding is `open` and `counters.remediation_rounds < state.max_remediation_rounds`, else `Command(goto="gate_signoff")`. `counters.challenge_rounds` counts executions. The node stores the `findings` artifact and writes the rows before routing, inside its `Step`.

## 4. Remediation (`nodes/remediate.py`, purpose `remediate`)

### 4.1 Responses

One gateway call per `open` finding, `remediate/respond.v1`: the finding (JSON), the current bodies of the sections its evidence names (every section when it names none), and the excerpts those sections may cite (`excerpts.for_section`; the developer sees its own artifacts). No knowledge passages: the respond call needs none. Output `FindingResponseDraft{decision: accept|dispute, action: str, evidence: list[str] = [], sections_to_regenerate: list[str] = []}`. Hook: a dispute carries at least one `[A:<alias>#<path>]` marker that resolves against the section excerpts (any alias of `excerpts.KIND_TO_REF`); `sections_to_regenerate` are template section ids; an accept has a non-empty `action`.

### 4.2 Regeneration and re-render

The union of `sections_to_regenerate` over accepted findings is regenerated through the SP3 write path, generalised: `document.write_section` takes a `SectionPrompts` (purpose, system, planning and writing prompt names, version) and an optional `findings_json`; the document node passes its SP3 prompts, the remediation node passes `remediate/plan_queries.v1` and `remediate/rewrite_section.v1` (which carry an extra fenced `findings` block holding the findings that name the section) under purpose `remediate`. Grounding checks, one regeneration and the banner apply unchanged. The node then stores a new `document_sections` (the untouched sections copied, the regenerated ones replaced), calls `render_document` and `check_completeness`, and stores new `document_md`, `document_html` and `completeness` refs. A round that regenerates nothing re-renders nothing.

### 4.3 Responses storage

`FindingResponse` gains `round: int` and `sections_regenerated: list[str]`; the `responses` artifact (`ResponsesList{round, responses}`) is cumulative. Summary: `{n_responses, n_accepted, n_disputed, n_sections_regenerated, round}`. Each finding row gets `status` (`accepted` or `disputed`), `response` (the action) and `response_evidence`. `counters.remediation_rounds` increments; the node returns `Command(goto="challenge")`.

## 5. Loop, bound and replay

- Rounds: challenge round 1 is the initial review; a remediation round is `remediate` followed by the challenger re-review. Worst case `document -> challenge -> remediate -> challenge -> remediate -> challenge -> gate_signoff`; early exit when no finding is `open`.
- Bound: `Brief.remediation_rounds: int = 2` (0 to 3; 0 means review only) copied by `intake` to `RunState.max_remediation_rounds`. Umbrella 5.2 ("configurable in the plan") is amended: the bound is a process knob of the brief, not part of the `ModelingPlan` the challenger reviews.
- Graph edges: `(document, challenge)` and `(remediate, challenge)` fixed; `challenge` routes by `Command` to `remediate` or `gate_signoff`; the SP2 edges `(challenge, remediate)` and `(remediate, gate_signoff)` are removed.
- Replay without `--rerun-llm`: `challenge` copies the original manifest's `findings` (final statuses) and inserts the rows, routing to `remediate` when the original has a `responses` ref and `counters.remediation_rounds == 0`, else to `gate_signoff`; `remediate` copies `responses`, sets the counter to 1, does not re-render (the document node already copied the final `document_sections`), and routes to `challenge`, which copies again (same artifact id) and routes to the gate. Default replay stays byte-identical on every deterministic ref.
- `--rerun-llm`: both nodes run the loop again. `LLM_DERIVED_REFS` gains `findings` and `responses`; `compare_refs` lists every LLM-derived ref the original carries as `regenerated` (not only those in `DETERMINISTIC_REFS`); `DETERMINISTIC_REFS` is unchanged.

## 6. Sign-off gate

- `gate_summary("signoff")` adds `findings: {n_findings, by_severity, by_status, rounds}` (from the `findings` ref summary and `counters`) and `responses: refs["responses"].summary` when present; disputes stay visible.
- Policy (user decision): `record_approval` refuses `approve` at gate `signoff` while any finding of the run with severity `critical` or `high` has status `open` or `disputed` (`BlockingFindingsError(ApprovalError)` naming the finding ids and titles); `approve_with_conditions` and `reject` stay allowed. The check is a query on the `findings` table inside the approval transaction; the CLI prints the error and exits 2 as for every `ApprovalError`.

## 7. Prompts and the fake provider

Prompt files, all version 1 with front-matter `purpose, version, output_schema, description`:

| Prompt | Blocks (fenced, `<language> <name>`) | Output |
|---|---|---|
| `challenge/system.v1` | none (role, isolation statement, severity meanings, evidence grammar, SR 26-2 wording) | `ReviewDraft` |
| `challenge/plan_queries.v1` | `json sections` (titles), `json scope` (regulatory scope), `json statuses` (test id to status), `json prechecks` | `ChallengeQueryPlan` |
| `challenge/review.v1` | `markdown document`, `json validation`, `json plan`, `json passages`, `json prechecks` | `ReviewDraft` |
| `challenge/re_review.v1` | `json findings` (non-closed review findings with responses), `json changed_sections` (id to new body), `json validation`, `json plan` | `ReReviewDraft` |
| `remediate/system.v1` | none | `FindingResponseDraft` |
| `remediate/respond.v1` | `json finding`, `json sections` (id to body), `json excerpts` | `FindingResponseDraft` |
| `remediate/plan_queries.v1` | `json section`, `json findings` | `QueryPlan` |
| `remediate/rewrite_section.v1` | `json section`, `json excerpts`, `json passages`, `json findings`, optional `json failures` | `SectionDraft` |

The fake provider (`providers/fake.py`) scripts, keyed by schema class name:

- `ChallengeQueryPlan`: two fixed queries ("effective challenge and independent validation", "ongoing monitoring expectations").
- `ReviewDraft`: (a) always, a `medium`/`monitoring` finding "Monitoring thresholds are not quantified" with evidence `["monitoring", "[A:plan#thresholds.psi_warn]"]` (the fake document's monitoring section states no numbers); (b) when `plan.target.event` contains "proxy" and the limitations section body does not, a `medium`/`data` finding on the dataset proxies with evidence `["limitations", "[A:plan#target.event]"]`; (c) for every sentence of the performance section containing "was performed" and no `[A:` marker, a `high`/`performance` finding "Claimed test without artifact evidence" with evidence `["performance"]`.
- `ReReviewDraft`: an `accepted` finding whose evidence section is among `changed_sections` is `closed` ("the regenerated section addresses the finding"); an `accepted` one not regenerated stays `open`; a `disputed` one stays `disputed` ("dispute noted; visible at sign-off").
- `FindingResponseDraft`: a finding whose category is `data` and whose title names a proxy is disputed with evidence `["[A:plan#target.event]"]`; every other finding is accepted with `sections_to_regenerate` = the section ids in its evidence.
- `SectionDraft` with a `findings` block: the section's template rendered as in SP3, then one sentence per finding: for an evidence marker `[A:validation#tests[<id>].status]` the sentence "The validation test `<id>` has outcome <value> [A:validation#tests[<id>].status]." (so the undiscussed-test finding closes in round 2); otherwise "This revision addresses the reviewer's finding `<title>`." with the title in a code span (grounding reads no number inside one).

Scripts render only from the blocks; the fake stays a stand-in and the README says so.

## 8. Lineage additions

- Migration `0004_sp4.sql`: the four `findings` columns of 3.5.
- `findings.py`: `insert_findings` writes the new columns; `set_response` unchanged; new `set_verdict(conn, run_id, finding_id, *, status, verdict, closed_round)`; `list_findings` returns the new columns; new `blocking_findings(conn, run_id) -> list[dict]` (open or disputed, critical or high).
- `approvals.py`: `BlockingFindingsError`; `record_approval` calls `blocking_findings` for `approve` at `signoff`.
- No new table: responses live on the findings rows (SP2 design) and in the `responses` artifact.

## 9. Orchestrator wiring

- `agent_schemas.py`: `ChallengeQueryPlan`, `FindingDraft`, `ReviewDraft`, `Verdict`, `ReReviewDraft`, `FindingResponseDraft`.
- `schemas.py`: `Finding` and `FindingResponse` fields of 3.5 and 4.3; `Counters.challenge_rounds`; `RunState.max_remediation_rounds: int = 2`; `RefName` unchanged (`findings`, `responses` exist).
- `briefs.py`: `Brief.remediation_rounds: int = Field(default=2, ge=0, le=3)`; `intake` copies it.
- `graph.py`: edges of section 5; `challenge` and `remediate` return `Command`.
- `nodes/document.py`: `SectionPrompts` and the `findings_json` parameter; a `split_sections(markdown, template) -> dict[section_id, body]` helper shared with the challenger (the heading regex of the renderer).
- `nodes/gates.py`: sign-off summary of section 6.
- `replay.py`: section 5.
- CLI: `status` prints a `findings:` line (`n open/accepted/disputed/closed, max severity`) and `--json` includes the findings rows; `replay --rerun-llm` help names all four nodes; `approve` surfaces `BlockingFindingsError` like any `ApprovalError`.
- Worker and tools: unchanged.

## 10. Eval harness (`packages/evals`, `modelforge_evals`)

### 10.1 Layout

```
packages/evals/modelforge_evals/
  __init__.py, config.py (evals.yml model), cli.py (Typer app `modelforge-evals`),
  harness.py (drive a brief through the worker with scripted approvals),
  metrics.py (retrieval, grounding, scope, challenger, cost and latency),
  defects.py (planted-defect mutations and matching), probes.py (probe runs),
  report.py (EvalReport model, baseline comparison, Markdown summary)
packages/evals/tests/
evals/evals.yml            profiles, briefs, approvals, layers, tolerances, budgets
evals/goldens/retrieval.yaml   the knowledge golden questions (moved from packages/knowledge/tests, grown to about 20)
evals/goldens/defects.yaml     the planted-defect suite
evals/baselines/fake.json, default.json, ci-cheap.json
evals/reports/             git-ignored
```

The knowledge unit test reads `evals/goldens/retrieval.yaml` (single source).

### 10.2 Configuration (`evals/evals.yml`)

```yaml
briefs: {synthetic: examples/briefs/synthetic.yaml, polish: examples/briefs/polish-pd.yaml}
approvals:            # the harness approves in this order; approve falls back to
  - {gate: scope, decision: approve, approver: alice}        # approve_with_conditions
  - {gate: model, decision: accept, approver: bob}           # naming the blocking findings
  - {gate: signoff, decision: approve, approver: bob}        # when record_approval refuses
profiles:
  fake:     {brief: synthetic, layers: [retrieval, pipeline, challenger, cost], compare: exact, ignore: [latency_ms_p50, latency_ms_p95, wall_time_s]}
  default:  {brief: polish, layers: [retrieval, pipeline, challenger, cost], tolerances: {grounding_pass_rate: -0.10, retrieval_recall_at_5: -0.05, defect_recall: -0.17, defect_precision: -0.20, cost_usd: 1.50}, budgets: {cost_usd: 8.0, wall_time_s: 2700}}
  ci-cheap: {brief: polish, layers: [retrieval, pipeline, challenger, cost], tolerances: {grounding_pass_rate: -0.15, retrieval_recall_at_5: -0.05, defect_recall: -0.34, defect_precision: -0.25, cost_usd: 1.00}, budgets: {cost_usd: 5.0, wall_time_s: 2700}}
```

### 10.3 Metrics (per profile, one `EvalReport`)

- `retrieval_recall_at_5` over `evals/goldens/retrieval.yaml` through `modelforge_knowledge` directly (no run).
- From the pipeline run: `grounding_pass_rate = 1 - n_flagged_sections / n_sections`, `n_ungrounded_numbers`, `completeness_complete`, `scope_exact_match` (identity fields equal the brief's and the scope call succeeded on its first attempt), `challenge_rounds`, `remediation_rounds`, findings `by_source`, `by_severity`, `by_status` at sign-off, `n_sections_regenerated`, `signoff_decision`.
- Challenger quality (10.4): `defect_recall` per class and overall, `defect_precision`.
- Cost and latency from `llm_calls` and `steps`: `cost_usd` total and per purpose, `n_llm_calls`, `n_schema_retries`, tokens in and out, `latency_ms_p50`, `latency_ms_p95`, `wall_time_s` per LLM node and total.
- Versions: `orchestrator_version`, prompt names and hashes used, profile name and models.

### 10.4 Planted-defect suite (`evals/goldens/defects.yaml`)

Six cases, each a mutation of the completed run's rendered document and an expected finding signature `{category, section, check?}`:

| Class | Mutation | Signature |
|---|---|---|
| `removed_section` | drop the `## n. Ongoing monitoring plan` block | `check: missing_section:monitoring` |
| `swapped_number` | change one digit of the first grounded number in the performance section | `check: reverify:performance` |
| `claimed_absent_test` | append "An independent bootstrap stability test was performed and passed." to the performance section | `category: performance, section: performance` |
| `miscited_regulation` | replace the first `[K:<id>]` in the regulatory-context section with a citation id of an `ifrs9-ecl` chunk | `check: miscited_regulation:regulatory_context:*` |
| `undiscussed_breach` | remove every sentence of the performance section carrying a `tests[<id>]` marker for a failing test | `check: undiscussed_test:<id>` |
| `missing_limitation` | remove every sentence of the limitations section containing "proxy" | `category: data, section: limitations` |

A finding matches a signature when its `check` matches (glob) or when its category equals and the section id is in its evidence. Recall per class is 0 or 1; overall recall is the mean. Precision: findings on a mutated document that match neither its signature nor a baseline finding (the unmutated run's round-1 findings, keyed by `check` or by category and title) are false positives; `defect_precision = matched / (matched + false_positives)`.

### 10.5 Probe runs

Each defect case runs the real `challenge` node on a probe run: a `runs` row (requester `evals`, the base run's profile, brief, dataset ref, seed and cost cap), a `RunState` with `max_remediation_rounds = 0`, `plan` = the base plan, and refs `document_md` (the mutated document, stored and recorded on the probe run), `completeness` (recomputed by `check_completeness` on the mutated document), `validation`, `knowledge_index` and every artifact the document cites copied from the base run (recorded on the probe run so marker re-verification can look up kinds). The node's calls, findings rows and events are recorded on the probe run; the harness marks it `completed` afterwards (no manifest; an `eval.probe` event names the base run and the defect class). Probe runs are never replayed (`create_replay` would accept them, `original_manifest` would then fail; documented).

### 10.6 Report, baselines, CLI

- `EvalReport` (pydantic, no timestamps) is stored as an `eval_report` artifact recorded on the pipeline run and written to `evals/reports/<profile>-<run_id>.json`; a Markdown summary goes to stdout and to `$GITHUB_STEP_SUMMARY` when set.
- `modelforge-evals run --profile <name> [--brief <key>] [--layers a,b] [--baseline PATH] [--write-baseline PATH] [--report-dir DIR]`: exits 1 when a metric regresses beyond its tolerance or a budget is exceeded; `--write-baseline` writes the report's metrics as the new baseline. `modelforge-evals compare REPORT BASELINE` compares two files. `fake` compares exactly and ignores timing.
- Make targets `evals` (fake with the committed baseline) and `evals-live` (`PROFILE=default`).

### 10.7 CI

- `ci.yml` gains `make evals` after `make test` (offline, `fake`).
- New `evals.yml`: on push to `main`, nightly, and on the `run-evals` label; runs `modelforge-evals run --profile ci-cheap --baseline evals/baselines/ci-cheap.json` with `ANTHROPIC_API_KEY` from secrets and `MODELFORGE_COST_CAP_USD=8`, uploads the report. No remote exists, so the workflow is committed but unexercised here.

## 11. Live runs

With the user's authorisation (about 6 USD): one `default` run of the Polish brief through the eval harness (pipeline through the three gates, probes, report) and one `ci-cheap` run. The README table gains rows for both with findings, rounds, regenerated sections, cost and wall time; `evals/baselines/default.json` and `ci-cheap.json` are committed from the reports. Spend is recorded in the hand-off note. The live run needs the main checkout's `.env` (not present in the worktree); copy it or pass `ANTHROPIC_API_KEY` explicitly, and point `MODELFORGE_DATA_DIR` at the main checkout's `data/` to avoid re-downloading.

## 12. Testing

- Challenger: pre-checks 1 to 6 each with a positive and a negative case over a rendered fake document; the sentinel isolation test; evidence hook rejects each bad entry; routing to `remediate`/`gate_signoff` by open findings and bound; re-review closes a pre-check finding deterministically and applies LLM verdicts; parks on refusal; replay copies findings.
- Remediation: per-finding calls; dispute without evidence is retried then parks; accepted findings regenerate exactly the named sections and re-render; a round with nothing to regenerate re-renders nothing; responses rows and artifact; counter and routing; replay copies responses without re-rendering.
- Loop: the fake synthetic run ends with every finding closed or disputed and `remediation_rounds` of 1 or 2; a brief with `remediation_rounds: 0` goes straight to sign-off with open findings; the bound stops the loop with findings still open.
- Sign-off policy: `record_approval` refuses `approve` with an open high finding, accepts `approve_with_conditions`, and accepts `approve` once the finding is closed; the CLI exit code is 2.
- Replay: default replay byte-identical through the loop; `--rerun-llm` regenerates all four nodes, `regenerated` lists `document_md, document_html, completeness, findings, responses`, new `llm_calls` rows carry purposes `challenge` and `remediate`.
- Evals: config parsing; each mutation produces the expected signature on the fake document; matching and precision arithmetic; the harness drives the synthetic brief under `fake` and the report equals the committed baseline apart from timing; the CLI exits 1 on a planted regression; `retrieval_recall_at_5` equals 1.0 on the golden set.
- End to end: the existing CLI tests gain assertions for `findings`/`responses` refs and purposes `{scope, document, challenge, remediate}` in `llm_calls`; the Polish slow test asserts challenger findings, responses and `approve` succeeding (or the harness fallback).
- `make lint` with eight contracts; the no-pickle scan covers `packages/evals`.

## 13. Risks

- Local or small models may fail the evidence hook repeatedly and park at `challenge`; that is a measured result (parity table), and `remediation_rounds: 0` plus `approve_with_conditions` still seals a run.
- The planted-defect mutations are written against the fake document's wording; a change to `fake_sections.yaml` changes the baseline (the baseline is committed with it).
- The block-on-critical/high policy can refuse `approve` on live runs; the harness falls back to `approve_with_conditions` naming the findings, and the CLI error explains.
- Eval wall time in CI: the fake synthetic pipeline (about one minute) plus six probe challenges under `fake`; well under the test suite's seven minutes.
- `challenge` is reached by two fixed edges and routes by `Command`; the worker's resume path after a `Command` node was fixed in SP3 and a test pins resume after a `challenge` failure.

## 14. Umbrella spec amendments to record (closing docs commit, section 21)

- 5.2: loop semantics (initial review, then up to two remediate-plus-re-review rounds), bound in the brief (`remediation_rounds`, default 2), `Counters.challenge_rounds`.
- 8.3: pre-checks 5 and 6 added; evidence grammar; `source`, `check`, `verdict`, `closed_round` on findings; the re-review raises no new LLM findings.
- 8.4: one call per finding; regeneration through the shared write path under purpose `remediate`; re-render per round.
- 10.1: `findings` columns; sign-off `approve` blocked on open or disputed critical/high findings.
- 10.4: `--rerun-llm` covers all four LLM nodes; `regenerated` lists `findings` and `responses`.
- 14: the LLM judge deferred; layer 4 as built (harness, probe runs, planted defects); layout `packages/evals` plus `evals/`; the retrieval goldens moved.
- 17 row 4 as built.

## 15. Deferred and out of scope

The LLM judge of document quality (umbrella 14.1; the `judge` purpose stays routed), a hand-labelled finding set, a local-ollama row for SP4's features, scanning original column names on the scope call (the dictionary does not exist before `profile`), a `knowledge_index` summary naming the corpus documents (console, SP5), recorded real responses for integration tests.
