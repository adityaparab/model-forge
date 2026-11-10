# SP4: Challenger Agent, Remediation Loop and Eval Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `challenge` and `remediate` stubs with gateway agents (an isolated MRM-style challenger with deterministic pre-checks, and a bounded remediation loop that answers findings, regenerates sections and re-reviews), extend replay to all four LLM nodes, and add the `modelforge_evals` harness with committed baselines, so the Polish brief seals through the whole loop offline and every profile is measured the same way.

**Architecture:** `nodes/challenge.py` builds its model context from lineage artifacts only (rendered document, validation report, plan from `runs.plan_ref`, its own knowledge passages), runs six deterministic pre-checks (`prechecks.py`) over the rendered document, then the two-call review; it routes by `Command` to `remediate` or `gate_signoff`. `nodes/remediate.py` answers each open finding with one gateway call, regenerates accepted sections through the SP3 write path under new `remediate/*` prompts, re-renders, and routes back to `challenge` for the re-review. Findings and responses are cumulative artifacts plus `findings` rows; the sign-off `approve` is refused by `record_approval` while a critical or high finding is open or disputed. `packages/evals` drives briefs through the in-process worker, runs the real challenger on probe runs over planted defects, and compares an `EvalReport` with committed baselines.

**Tech Stack:** Python 3.12 (uv workspace), LangGraph 1.2 (`Command` routing), `langchain-core` 1.7 through `modelforge_gateway`, Jinja2 prompts, psycopg 3, Typer, pytest. No new third-party dependencies.

**Spec:** `docs/superpowers/specs/2026-11-10-sp4-challenger-remediation-evals-design.md` (binding); umbrella `docs/superpowers/specs/2026-11-09-modelforge-architecture-design.md` sections 5.2, 8.3, 8.4, 10.1, 14, 15, 20; `docs/superpowers/specs/2026-11-09-sp3-gateway-knowledge-agents-design.md` section 13; `docs/superpowers/notes/2026-11-09-sp3-handoff.md`.

## Global Constraints

- Python `>=3.12,<3.13`; one uv workspace; seven packages after Task 1 (`modelforge_evals` added). Layering `evals > orchestrator > (mcp | gateway) > knowledge > lineage > core` enforced by import-linter; `modelforge_evals` never imports `langchain_core`, `langgraph`, `mcp`, provider SDKs or HTTP clients directly; only `modelforge_gateway.providers` imports the LangChain integrations. Eight contracts after Task 1.
- All LLM traffic through `Gateway.complete` via `Step.llm`; purposes `challenge` and `remediate` (already routed in every profile). No direct provider SDK anywhere. `modelforge_core` is not modified.
- No `pickle`, `joblib.dump`, `cloudpickle` or `dill` in any `modelforge_*` package. No wall-clock value inside any artifact (durations allowed). Every function that uses randomness takes an explicit `seed` (none is expected in SP4).
- Prompt files are immutable once shipped on `main`: the SP3 `document/*.v1.md` and `scope/*.v1.md` files are not edited; new prompts are `challenge/*.v1.md` and `remediate/*.v1.md` with front-matter `purpose, version, output_schema, description`. SR 26-2 wording: superseded SR 11-7 and SR 21-8 in April 2026, excludes generative and agentic AI; never "extends SR 11-7 to LLMs". `packages/knowledge/tests/test_wording.py` scans `prompts/` recursively and so covers the new files.
- Redaction refuses and parks; never masks; never logs the matched text. The challenger's redaction list (brief `restricted_terms` plus original column names) is passed in `RedactionContext` only.
- MCP servers, tool versions, contract snapshots and goldens are unchanged.
- Tests that need Postgres carry `@pytest.mark.db`. Postgres runs on `localhost:5432` from the SP2 worktree's Compose project: never run `make db-up` or `docker compose up` from this worktree. Run everything with `uv run` from the worktree root (the venv exists). The dataset cache lives in the main checkout: set `MODELFORGE_DATA_DIR` pointing at the main checkout's `data/` directory before any Polish test.
- The repository stays employer-neutral: no company or bank names in committed files.
- Commit after every task, conventional-commit style, ending with the trailer of the model that authored the commit (`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` for Opus implementers).

## Prompt layout contract (Tasks 5, 7, 8, 9 depend on it)

User messages are Markdown with fenced blocks whose info string is `<language> <name>`; the fake provider reads blocks by `<name>` (`fake.parse_blocks`) and nothing else in the wording.

| Prompt | Blocks (in order) | Output schema |
|---|---|---|
| `challenge/plan_queries.v1` | ```` ```json sections ```` (list of section titles), ```` ```json scope ```` (the plan's `regulatory_scope`), ```` ```json statuses ```` (`{test_id: status}`), ```` ```json prechecks ```` (list of pre-check findings as JSON) | `ChallengeQueryPlan` |
| `challenge/review.v1` | ```` ```markdown document ````, ```` ```json validation ```` (the `validation_report` excerpt), ```` ```json plan ````, ```` ```json passages ````, ```` ```json prechecks ```` | `ReviewDraft` |
| `challenge/re_review.v1` | ```` ```json findings ```` (non-closed review findings with `status`, `response`, `response_evidence`), ```` ```json changed_sections ```` (`{section_id: body}`), ```` ```json validation ````, ```` ```json plan ```` | `ReReviewDraft` |
| `remediate/respond.v1` | ```` ```json finding ````, ```` ```json sections ```` (`{section_id: body}`), ```` ```json excerpts ```` (`{alias: excerpt}`) | `FindingResponseDraft` |
| `remediate/plan_queries.v1` | ```` ```json section ```` (as the document agent's), ```` ```json findings ```` | `QueryPlan` |
| `remediate/rewrite_section.v1` | ```` ```json section ````, ```` ```json excerpts ````, ```` ```json passages ````, ```` ```json findings ````, on regeneration ```` ```json failures ```` | `SectionDraft` |

Evidence entries everywhere: a template section id, `[A:<alias>#<path>]` (challenger aliases `validation` and `plan` only; developer aliases any of `excerpts.KIND_TO_REF` values and `plan`), or `[K:<citation_id>]`.

## Review Focus

1. **A rendered document whose section titles or numbering do not match the template** (a hand-edited or mutated document): `split_sections` maps by title, ignores the number, and a missing title is simply absent, never an exception. Test: Task 4 `test_split_sections_ignores_numbering_and_reports_missing`.
2. **A model that returns evidence naming an unknown section, an alias outside its allowance, or an unretrieved passage**: the hook rejects, the gateway retries with the message, then parks with kind `schema`; nothing is stored. Tests: Task 7 `test_review_hook_names_each_bad_evidence_entry`, `test_challenge_parks_after_persistent_bad_evidence`.
3. **A re-review that omits a verdict or names an unknown finding id**: rejected by the hook (coverage and ids), retried, then parks. Test: Task 7 `test_re_review_hook_requires_one_verdict_per_finding`.
4. **A brief with `remediation_rounds: 0`, or a run whose every finding is disputed**: `challenge` routes straight to `gate_signoff`; at sign-off `approve` is refused when a high finding is open or disputed and the error names it; `approve_with_conditions` seals the run. Tests: Task 9 `test_loop_exits_with_zero_rounds_and_when_everything_is_disputed`, Task 10 `test_cli_signoff_approve_blocked_by_open_high_finding`.
5. **A rewritten section that mentions a finding id such as `F-3` in prose**: grounding would read `3` as an ungrounded number; the rewrite prompt and the fake put ids and titles in inline code spans. Test: Task 8 `test_fake_rewrite_quotes_finding_titles_in_code_spans`.
6. **A probe case whose mutation changes nothing** (the base document cites no failing test): the case reports `recall: None` and is excluded from the mean instead of counting as a miss. Test: Task 12 `test_inapplicable_defect_case_is_excluded_from_recall`.

---

## File structure

```
pyproject.toml                               seventh member; layers and contracts; pytest testpaths (Task 1)
Dockerfile                                   + packages/evals/pyproject.toml in the dependency layer (Task 1)
.gitignore                                   + evals/reports/ (Task 1)
Makefile                                     + evals, evals-live targets (Task 13)
.github/workflows/ci.yml                     + make evals (Task 13); .github/workflows/evals.yml (Task 13)
packages/lineage/modelforge_lineage/migrations/0004_sp4.sql, findings.py, approvals.py            (Task 2)
packages/orchestrator/modelforge_orchestrator/
  agent_schemas.py, schemas.py, briefs.py, nodes/intake.py                                       (Task 3)
  sections.py (split_sections), nodes/document.py (SectionPrompts, write_section generalised,
    restricted_terms, render_and_check)                                                          (Task 4)
  prechecks.py                                                                                   (Task 6)
  nodes/challenge.py, graph.py (edge), fixtures/findings.yaml deleted                            (Task 7)
  nodes/remediate.py, graph.py (edges), nodes/gates.py (sign-off summary)                        (Task 9)
  replay.py, cli.py (status findings line and --json findings, replay help)                      (Task 10)
  submit.py (create_run_from_brief, shared with the evals harness), cli.py (uses it)             (Task 12)
packages/gateway/modelforge_gateway/providers/fake.py                                            (Tasks 5, 8)
prompts/challenge/{system,plan_queries,review,re_review}.v1.md                                   (Task 5)
prompts/remediate/{system,respond,plan_queries,rewrite_section}.v1.md                            (Task 8)
packages/evals/pyproject.toml, modelforge_evals/__init__.py                                      (Task 1)
packages/evals/modelforge_evals/config.py, metrics.py, report.py                                 (Task 11)
packages/evals/modelforge_evals/harness.py, defects.py, probes.py                                (Task 12)
packages/evals/modelforge_evals/cli.py                                                           (Task 13)
evals/evals.yml, evals/goldens/retrieval.yaml (moved from packages/knowledge/tests)              (Task 11)
evals/goldens/defects.yaml                                                                       (Task 12)
evals/baselines/fake.json                                                                        (Task 13)
README.md                                                                                        (Task 14)
```

---

### Task 1: Workspace: the `evals` package skeleton, import-linter contracts, Dockerfile, gitignore

**Files:**
- Create: `packages/evals/pyproject.toml`, `packages/evals/modelforge_evals/__init__.py`, `packages/evals/tests/test_version.py`
- Modify: `pyproject.toml` (dependencies, `[tool.uv.sources]`, `testpaths`, import-linter), `Dockerfile`, `.gitignore`, `uv.lock` (via `uv sync`)

**Interfaces:**
- Produces: package `modelforge_evals` (`__version__ = "0.1.0"`), distribution `modelforge-evals` depending on `modelforge-orchestrator`, `modelforge-gateway`, `modelforge-knowledge`, `modelforge-lineage`, `modelforge-core`, `typer>=0.28`, `pyyaml>=6`, `pydantic>=2.7`; console script `modelforge-evals = "modelforge_evals.cli:app"` (the module arrives in Task 13; until then the script is declared but unused).
- Import-linter: layers `["modelforge_evals", "modelforge_orchestrator", "modelforge_mcp | modelforge_gateway", "modelforge_knowledge", "modelforge_lineage", "modelforge_core"]`; contracts 6 and 7 add `modelforge_evals` to `source_modules`; `root_packages` adds it; new contract 8 `name = "evals reach models and tools only through the orchestrator and gateway"`, `type = "forbidden"`, `source_modules = ["modelforge_evals"]`, `forbidden_modules = ["langchain_core", "langgraph", "mcp", "langchain_anthropic", "langchain_ollama", "langchain_openai"]`.

- [ ] **Step 1: Write the failing test** `packages/evals/tests/test_version.py`: `test_version(): assert modelforge_evals.__version__ == "0.1.0"`, mirroring `packages/knowledge/tests/test_version.py`.
- [ ] **Step 2: Run** `uv run pytest packages/evals -q`; expected: collection error (no package).
- [ ] **Step 3: Create the package** (pyproject as `packages/knowledge/pyproject.toml` with the dependencies above and the script entry), add the workspace member and the four root `pyproject.toml` edits, add `COPY packages/evals/pyproject.toml packages/evals/pyproject.toml` to the Dockerfile's dependency layer, add `evals/reports/` under "local data and run outputs" in `.gitignore`, then `uv sync --all-groups`.
- [ ] **Step 4: Verify** `uv run pytest packages/evals -q` passes; `make lint` passes and `uv run lint-imports` reports eight contracts kept; `docker build -t modelforge:sp4 .` succeeds (skip if Docker is not running and say so in the commit body).
- [ ] **Step 5: Commit** `chore(workspace): add the evals package, eighth import contract and Dockerfile member`.

---

### Task 2: Lineage: migration 0004, findings columns and verdicts, sign-off blocking policy

**Files:**
- Create: `packages/lineage/modelforge_lineage/migrations/0004_sp4.sql`
- Modify: `packages/lineage/modelforge_lineage/findings.py`, `approvals.py`
- Test: `packages/lineage/tests/test_findings.py`, `test_approvals.py`

**Interfaces:**
- Migration: `alter table findings add column source text not null default 'review', add column check_key text null, add column verdict text null, add column closed_round int null;` (`check` is reserved in SQL; the column is `check_key`, and the spec's `check` field is named `check_key` everywhere: as-built amendment for the hand-off note).
- `insert_findings(conn, run_id, rows)` writes `source` (default `"review"` when the row lacks it) and `check_key` (default `None`); `verdict` and `closed_round` start null.
- `set_verdict(conn, run_id, finding_id, *, status: str, verdict: str, closed_round: int | None) -> None` (raises `FindingNotFound` on no row).
- `blocking_findings(conn, run_id) -> list[dict[str, Any]]`: rows with `status in ('open', 'disputed') and severity in ('critical', 'high')`, ordered by id.
- `approvals.BlockingFindingsError(ApprovalError)`; `record_approval` raises it for `decision.gate == "signoff" and decision.decision == "approve"` when `blocking_findings` is non-empty, before any write, with the message `run {run_id}: approve is refused while findings are open or disputed at critical or high severity: {F-n (severity) title, ...}; use approve_with_conditions or reject` where `F-n` is the id after the `run_id/` prefix.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.db
def test_findings_new_columns_verdict_and_blocking(db: Database) -> None:
    with db.transaction() as conn:
        create_test_run(conn)
        insert_findings(conn, "run_a", [{**_row("F-1"), "source": "precheck", "check_key": "undiscussed_test:cal.hl", "severity": "high"}, _row("F-2")])
    with db.connection() as conn:
        rows = list_findings(conn, "run_a")
    assert (rows[0]["source"], rows[0]["check_key"], rows[0]["verdict"], rows[0]["closed_round"]) == ("precheck", "undiscussed_test:cal.hl", None, None)
    assert (rows[1]["source"], rows[1]["check_key"]) == ("review", None)
    with db.connection() as conn:
        assert [r["id"] for r in blocking_findings(conn, "run_a")] == ["run_a/F-1"]   # F-2 is medium
    with db.transaction() as conn:
        set_verdict(conn, "run_a", "run_a/F-1", status="closed", verdict="check passes after remediation", closed_round=2)
    with db.connection() as conn:
        row = list_findings(conn, "run_a")[0]
        assert (row["status"], row["verdict"], row["closed_round"]) == ("closed", "check passes after remediation", 2)
        assert blocking_findings(conn, "run_a") == []
    with db.transaction() as conn, pytest.raises(FindingNotFound):
        set_verdict(conn, "run_a", "run_a/F-9", status="closed", verdict="x", closed_round=2)

@pytest.mark.db
def test_signoff_approve_blocked_by_open_high_finding(db: Database) -> None:
    # create_test_run; set_status awaiting_signoff; insert a high open finding "Calibration drifts"
    with db.transaction() as conn, pytest.raises(BlockingFindingsError) as exc:
        record_approval(conn, "run_a", GateDecision(gate="signoff", decision="approve", approver="bob"), current_plan=None)
    assert "F-1 (high) Calibration drifts" in str(exc.value) and "approve_with_conditions" in str(exc.value)
    # approve_with_conditions with one condition is recorded; a disputed high finding also blocks approve; after set_verdict closed, approve is recorded
```

- [ ] **Step 2: Run** `uv run pytest packages/lineage/tests/test_findings.py packages/lineage/tests/test_approvals.py -q`; expected: ImportError / column errors.
- [ ] **Step 3: Implement** the migration, the three repository functions and the policy check (query inside the open transaction, after the four-eyes check).
- [ ] **Step 4: Verify** `uv run pytest packages/lineage -q` passes (the migrate test's idempotency assertion still holds with four files) and `make lint` passes.
- [ ] **Step 5: Commit** `feat(lineage): findings source, check key, verdicts and the sign-off blocking policy`.

---

### Task 3: Orchestrator schemas, agent schemas, brief bound, intake, state

**Files:**
- Modify: `packages/orchestrator/modelforge_orchestrator/agent_schemas.py`, `schemas.py`, `briefs.py`, `nodes/intake.py`, `nodes/remediate.py` (stub: `FindingResponse(round=1, ...)`), `nodes/challenge.py` (stub: pass `source="review"` explicitly is not needed; defaults cover it)
- Test: `packages/orchestrator/tests/test_agent_schemas.py`, `test_schemas.py`, `test_briefs.py`, `test_nodes_early.py`

**Interfaces (produces):**
- `agent_schemas.py`: `ChallengeQueryPlan(StrictModel)`: `queries: list[str] = Field(max_length=5)`. `FindingDraft(StrictModel)`: `severity: Literal["critical", "high", "medium", "low"]`, `category: Literal[the eight of Finding]`, `title: str`, `description: str`, `evidence: list[str]`, `required_action: str`. `ReviewDraft(StrictModel)`: `findings: list[FindingDraft]`. `Verdict(StrictModel)`: `finding_id: str`, `status: Literal["closed", "open", "disputed"]`, `note: str`. `ReReviewDraft(StrictModel)`: `verdicts: list[Verdict]`. `FindingResponseDraft(StrictModel)`: `decision: Literal["accept", "dispute"]`, `action: str`, `evidence: list[str] = []`, `sections_to_regenerate: list[str] = []`.
- `schemas.py`: `Finding` gains `source: Literal["precheck", "review"] = "review"`, `check_key: str | None = None`, `verdict: str | None = None`, `closed_round: int | None = None`. `FindingResponse` gains `round: int` (required) and `sections_regenerated: list[str] = Field(default_factory=list)`. `Counters` gains `challenge_rounds: int = 0`. `RunState` gains `max_remediation_rounds: int = 2`. `FindingsList` and `ResponsesList` shapes unchanged (`round` = latest round; lists cumulative).
- `briefs.py`: `Brief.remediation_rounds: int = Field(default=2, ge=0, le=3)`.
- `nodes/intake.py`: the update becomes `{"max_remediation_rounds": brief.remediation_rounds}`.

- [ ] **Step 1: Write the failing tests**: `test_agent_schemas.py` validates one instance of each new schema and rejects `ChallengeQueryPlan(queries=[6 strings])` and `Verdict(status="maybe")`; `test_schemas.py::test_finding_and_response_new_fields` (defaults as above; `FindingResponse` without `round` raises `ValidationError`; `RunState(...).max_remediation_rounds == 2` and `Counters().challenge_rounds == 0`); `test_briefs.py::test_remediation_rounds_bound` (`remediation_rounds: 4` and `-1` raise `BriefError`; absent gives 2); `test_nodes_early.py::test_intake_copies_remediation_rounds` (a synthetic brief with `remediation_rounds: 1` yields `apply_update(state, intake(...)).max_remediation_rounds == 1`).
- [ ] **Step 2: Run** `uv run pytest packages/orchestrator/tests/test_agent_schemas.py packages/orchestrator/tests/test_schemas.py packages/orchestrator/tests/test_briefs.py packages/orchestrator/tests/test_nodes_early.py -q`; expected: failures on the new names.
- [ ] **Step 3: Implement**; update the remediate stub's `FindingResponse(..., round=1)`.
- [ ] **Step 4: Verify** `uv run pytest packages/orchestrator -q -m "not slow"` and `make lint` pass. The worker's checkpoint allowlist (`STATE_MODULES`) is unchanged: the new state fields live in `schemas.py`, and the `agent_schemas` models never enter the checkpointed state.
- [ ] **Step 5: Commit** `feat(orchestrator): finding and response fields, challenger and remediation schemas, remediation_rounds bound`.

---

### Task 4: Shared section helpers and the generalised write path

**Files:**
- Create: `packages/orchestrator/modelforge_orchestrator/sections.py`
- Modify: `packages/orchestrator/modelforge_orchestrator/nodes/document.py`
- Test: `packages/orchestrator/tests/test_sections.py` (new), `test_document_agent.py` (unchanged assertions must still pass)

**Interfaces (produces):**
- `sections.py`: `SECTION_HEADING = re.compile(r"^## (\d+)\. (.+?)\s*$", re.MULTILINE)`; `split_sections(markdown: str, template: TemplateSections) -> dict[str, str]` mapping each template section id whose title heads a `## n. Title` block to that block's body (stripped; the heading line excluded; the banner line kept); sections whose title is absent are absent from the dict; headings whose title matches no template section are ignored. `section_sentences(body: str) -> list[str]` re-exporting `grounding._sentences` as a public helper (rename `_sentences` to `sentences` in `grounding.py` and keep an alias).
- `nodes/document.py`: `SectionPrompts(StrictModel)`: `purpose: Purpose`, `system: str`, `plan_queries: str`, `write: str`, `version: int`; `DOCUMENT_PROMPTS = SectionPrompts(purpose="document", system="document/system", plan_queries="document/plan_queries", write="document/write_section", version=1)`. `write_section(step, deps, state, spec, context, restricted_terms, *, prompts: SectionPrompts = DOCUMENT_PROMPTS, findings_json: str | None = None) -> SectionOutcome`: the request helper takes `prompts` and passes `findings_json=findings_json` to the planning and writing templates (the SP3 templates ignore the extra variable; Jinja renders unknown variables as empty, and the registry's `render` already accepts arbitrary keyword values). `restricted_terms(deps, state) -> list[str]` (public; was `_restricted_terms`). `render_and_check(step: Step, state: RunState, sections_ref: ArtifactRef) -> dict[RefName, ArtifactRef]` returning `{"document_sections", "document_md", "document_html", "completeness"}` (the render title and context exactly as `document()` builds them today); `document()` calls it.
- Consumes: `TemplateSections`, `_SECTION_HEADING` semantics of the renderer (`## n. Title`).

- [ ] **Step 1: Write the failing tests**

```python
def test_split_sections_ignores_numbering_and_reports_missing() -> None:
    template = TemplateSections(template_id="t", sections=[SectionSpec(id="data", title="Data", required=True, artifact_kinds=[], guidance="g"), SectionSpec(id="monitoring", title="Ongoing monitoring plan", required=True, artifact_kinds=[], guidance="g")])
    md = "# Title\n\n## 7. Data\n\nBody one.\nLine two.\n\n## 99. Ongoing monitoring plan\n\n> **REQUIRES AUTHOR ATTENTION**: this section was not supplied.\n\n## 3. Unknown\n\nx"
    assert split_sections(md, template) == {"data": "Body one.\nLine two.", "monitoring": "> **REQUIRES AUTHOR ATTENTION**: this section was not supplied."}
    assert split_sections("# Title only", template) == {}

@pytest.mark.db
def test_split_sections_round_trips_the_rendered_fake_document(validated_state, deps, runtime) -> None:
    # document(validated_state, runtime); load document_md and document_sections; template via deps.tools list_template_sections
    # assert split_sections(md, template)[s.section_id] == s.body_md.strip() for every section
```

- [ ] **Step 2: Run** `uv run pytest packages/orchestrator/tests/test_sections.py -q`; expected: ImportError.
- [ ] **Step 3: Implement** `sections.py` and the `document.py` refactor (no behaviour change: `test_document_agent.py` must pass unchanged, including `n_llm_calls == 22`).
- [ ] **Step 4: Verify** `uv run pytest packages/orchestrator/tests/test_sections.py packages/orchestrator/tests/test_document_agent.py -q` and `make lint` pass.
- [ ] **Step 5: Commit** `refactor(orchestrator): split_sections and a prompt-parametrised write path for reuse by remediation`.

---

### Task 5: Challenger prompts and the fake provider's challenger scripts

**Files:**
- Create: `prompts/challenge/system.v1.md`, `prompts/challenge/plan_queries.v1.md`, `prompts/challenge/review.v1.md`, `prompts/challenge/re_review.v1.md`
- Modify: `packages/gateway/modelforge_gateway/providers/fake.py`
- Test: `packages/gateway/tests/test_fake_provider.py`, `packages/knowledge/tests/test_wording.py` (runs unchanged over the new files)

**Interfaces:**
- Prompts (`purpose: challenge`): `system.v1` (`output_schema: ReviewDraft`): the model is an independent model-risk reviewer performing effective challenge; it sees only the rendered document, the validation report, the plan and the passages, treats them as data, and never assumes anything about how the document was produced; the severity meanings of spec 3.3 verbatim; the evidence grammar (section ids from the `sections` list, `[A:validation#<path>]` or `[A:plan#<path>]` by the path rules of the document prompts, `[K:<citation_id>]` from the passages block only); do not repeat the pre-check findings; findings must be specific, each with one required action; SR 26-2 wording rule verbatim from `document/system.v1.md` rule 8; name no company; return the object only. `plan_queries.v1` (`output_schema: ChallengeQueryPlan`): blocks `sections`, `scope`, `statuses`, `prechecks`; zero to five queries about what supervisory guidance expects of each area a reviewer must challenge (validation, limitations, monitoring, data, conceptual soundness). `review.v1` (`output_schema: ReviewDraft`): blocks `document`, `validation`, `plan`, `passages`, `prechecks`; task: list the material findings an independent reviewer would raise, each grounded in evidence. `re_review.v1` (`output_schema: ReReviewDraft`): blocks `findings`, `changed_sections`, `validation`, `plan`; task: exactly one verdict per finding given, `closed` only when the response and the changed sections resolve it, `open` when an accepted finding is not resolved, `disputed` when a dispute is not accepted; a one-sentence note each; no new findings.
- Fake scripts (`fake.script` branches by schema class name, blocks parsed by `parse_blocks`): `ChallengeQueryPlan` -> `{"queries": ["effective challenge and independent validation", "ongoing monitoring expectations"]}`. `ReviewDraft` -> findings in this order: (a) always `{"severity": "medium", "category": "monitoring", "title": "Monitoring thresholds are not quantified", "description": "The monitoring section names the metrics but states no numeric thresholds or escalation triggers.", "evidence": ["monitoring", "[A:plan#thresholds.psi_warn]"], "required_action": "State the numeric thresholds and the escalation path for each monitored metric."}`; (b) when `"proxy" in plan["target"]["event"].lower()` and the limitations section body (from the `document` block, split on `## n. Limitations and weaknesses`) does not contain `proxy`: `{"severity": "medium", "category": "data", "title": "Dataset proxies are not disclosed as limitations", "evidence": ["limitations", "[A:plan#target.event]"], ...}`; (c) for every sentence of the performance section containing `was performed` and no `[A:`: `{"severity": "high", "category": "performance", "title": "Claimed test without artifact evidence", "evidence": ["performance"], ...}`; (d) always `{"severity": "low", "category": "governance", "title": "Approver identities are not named in the governance section", "evidence": ["governance"], ...}`. `ReReviewDraft` -> one verdict per entry of the `findings` block: status `accepted` and some evidence section id in `changed_sections` -> `closed`, note "the regenerated section addresses the finding"; `accepted` otherwise -> `open`, note "accepted but not remediated"; `disputed` -> `disputed`, note "dispute noted; remains visible at sign-off".
- `_infer_schema_name` (used by `_generate` only) gains: `prechecks` and `document` blocks -> `ReviewDraft`; `changed_sections` -> `ReReviewDraft`; `statuses` -> `ChallengeQueryPlan`.

- [ ] **Step 1: Write the failing tests** in `test_fake_provider.py`: `test_fake_scripts_challenge_query_plan`, `test_fake_review_raises_the_scripted_findings` (a constructed `document` block with a limitations section lacking "proxy", a performance section with "A bootstrap test was performed and passed.", plan JSON whose target event says "proxy"; expect four findings in order (a), (b), (c), (d) with the evidence above), `test_fake_review_omits_proxy_finding_when_limitations_mention_it`, `test_fake_re_review_verdicts_by_status_and_changed_sections` (three findings: accepted+changed -> closed, accepted+unchanged -> open, disputed -> disputed). Build the prompt text by hand with fenced blocks and call `ScriptedChatModel().with_structured_output(Schema, include_raw=True).invoke([HumanMessage(text)])`.
- [ ] **Step 2: Run** `uv run pytest packages/gateway/tests/test_fake_provider.py -q`; expected: `GatewayError: the fake provider has no script for ReviewDraft`.
- [ ] **Step 3: Write the four prompts and the scripts.** The fake imports `ReviewDraft` etc. by name only (the gateway cannot import orchestrator schemas: the fake validates against the schema class it is given, exactly as today).
- [ ] **Step 4: Verify** `uv run pytest packages/gateway packages/knowledge/tests/test_wording.py -q` and `make lint` pass.
- [ ] **Step 5: Commit** `feat(gateway): challenger prompts and fake challenger scripts`.

---

### Task 6: Deterministic pre-checks

**Files:**
- Create: `packages/orchestrator/modelforge_orchestrator/prechecks.py`
- Test: `packages/orchestrator/tests/test_prechecks.py`

**Interfaces (produces):**
- `PrecheckFinding(StrictModel)`: `check_key: str`, `severity`, `category`, `title: str`, `description: str`, `evidence: list[str]`, `required_action: str` (severity and category literals as `Finding`).
- `PrecheckContext(StrictModel, arbitrary types)`: `sections: dict[str, str]` (id to body), `template: TemplateSections`, `completeness: CompletenessReport`, `validation: dict[str, Any]` (the `validation_report` excerpt), `validation_id: str`, `artifact_kinds: dict[str, str]` (artifact id to kind for the run), `load_artifact: Callable[[str], Any]` (raw JSON by id), `passages: dict[str, tuple[str, str]]` (citation id to `(doc_id, text)`).
- `run_prechecks(ctx: PrecheckContext) -> list[PrecheckFinding]` in the table order of spec 3.2, each check's findings in section/test order. Keys and severities exactly as the spec table: `missing_section:<id>` (critical, documentation; required sections absent from `ctx.sections` or whose body equals `BANNER`, plus every id in `completeness.missing_sections`), `bannered_section:<id>` (high, documentation; body starts with `BANNER`), `undiscussed_test:<test_id>` (high for `fail`, medium for `warn`; discussed means `tests[<test_id>]` appears inside an `[A:` marker anywhere in the document, or the test's `name` or `test_id` appears case-insensitively in any section body), `unaddressed_na:<test_id>` (high; for `status == "not_applicable"`: some sentence names the test and contains `not applicable`, `not_applicable` or `not available`), `reverify:<id>` (high; `grounding.check_section(body, load_artifact=lambda i: excerpt(ctx.artifact_kinds[i], ctx.load_artifact(i)), retrieved=set(ctx.passages))` not `ok`; description lists the failing items with the report's four list names; an id missing from `artifact_kinds` is an unresolved marker), `miscited_regulation:<id>:<n>` (medium; `n` is the 1-based sentence ordinal within the section; a sentence matching `grounding.REGULATION_NAME` with a `[K:<cid>]` whose `cid` is unknown, or whose `doc_id` is not the named regulation's and whose text does not contain the regulation name; the map `REGULATION_DOCS = {"SR 11-7": "sr11-7", "SR 26-2": "sr26-2", "OCC": "occ-2026-13", "CECL": "cecl-asc-326", "ASC 326": "cecl-asc-326", "IFRS 9": "ifrs9-ecl", "Basel": "basel-irb"}` keyed by the first matching prefix of the regulation name). Evidence as the spec table: section ids and `[A:validation#tests[<test_id>].status]` / `.message` markers with the alias form (expanded by the node).
- Consumes: `BANNER`, `MARKER` from the doc-renderer module; `grounding.check_section`, `grounding.REGULATION_NAME`, `sections.section_sentences`; `excerpts.excerpt`.

- [ ] **Step 1: Write the failing tests**: one positive and one negative case per check over hand-built contexts (`sections` dicts, a `TemplateSections` with the eleven ids, a `CompletenessReport`, a validation excerpt with tests `disc.auc.oot` pass, `cal.hl` warn (name "Hosmer-Lemeshow test"), `stab.psi.score` fail, `sens.signs` not_applicable (message "No sensitivity results exist"), an `artifact_kinds` map and `load_artifact` over an in-memory dict with a `discrimination` artifact `{"by_split": [{"split": "oot", "auc": 0.874}]}`, passages `{"k_aaa": ("sr11-7", "...effective challenge..."), "k_bbb": ("ifrs9-ecl", "...twelve months...")}`): `test_missing_and_bannered_sections`, `test_undiscussed_tests_by_marker_name_or_id` (marker `[A:<validation_id>#tests[cal.hl].status]` discusses `cal.hl`; "Hosmer-Lemeshow test" discusses it; "stab.psi.score" bare discusses it; a document mentioning none raises `undiscussed_test:cal.hl` medium and `undiscussed_test:stab.psi.score` high with evidence `["performance", "[A:validation#tests[stab.psi.score].status]"]`), `test_unaddressed_not_applicable`, `test_reverify_catches_a_swapped_number_and_unknown_passage` (`0.874 [A:<disc_id>#by_split.oot.auc]` passes; `0.875` fails with the description naming `0.875`; `[K:k_zzz]` fails), `test_miscited_regulation` (`SR 11-7 requires effective challenge [K:k_aaa].` passes; `... [K:k_bbb].` fails with key `miscited_regulation:regulatory_context:1`; `SR 26-2 superseded SR 11-7 [K:k_sr26]` where the sr26-2 passage text names SR 11-7 passes), `test_prechecks_order_and_keys_are_stable` (same context twice gives equal lists).
- [ ] **Step 2: Run** `uv run pytest packages/orchestrator/tests/test_prechecks.py -q`; expected: ImportError.
- [ ] **Step 3: Implement** `prechecks.py`.
- [ ] **Step 4: Verify** the file passes and `make lint` passes.
- [ ] **Step 5: Commit** `feat(orchestrator): six deterministic challenger pre-checks`.

---

### Task 7: The challenger node

**Files:**
- Modify: `packages/orchestrator/modelforge_orchestrator/nodes/challenge.py` (rewrite), `graph.py` (remove the `(challenge, remediate)` edge; `challenge` routes by `Command`), `packages/orchestrator/tests/conftest.py` (`signed_off_state` applies `Command` updates; new `recording_runtime` fixture), `tests/test_stubs.py` (delete `test_findings_depend_on_calibration_status`; the combined test applies `challenge(...).update`)
- Delete: `packages/orchestrator/modelforge_orchestrator/fixtures/findings.yaml`
- Test: `packages/orchestrator/tests/test_challenger.py`, `test_challenger_isolation.py`

**Interfaces:**
- Consumes: Tasks 3 to 6; `runs.get_run` (for `plan_ref`), `steps.list_artifacts`, `KnowledgeIndex` (from `modelforge_knowledge.index`), `insert_findings`, `set_verdict`, `search_regulation`, `list_template_sections`.
- Produces: `CHALLENGER_ALIASES = ("validation", "plan")`; `ChallengerContext(StrictModel)` with `document_md: str`, `sections: dict[str, str]`, `template: TemplateSections`, `completeness: CompletenessReport`, `validation_excerpt: dict[str, Any]`, `validation_id: str`, `plan: ModelingPlan`, `plan_id: str`, `artifact_kinds: dict[str, str]`, `passages: dict[str, tuple[str, str]]`; `challenger_context(state: RunState, deps: Deps, step: Step) -> ChallengerContext` (reads exactly: `refs["document_md"]`, `refs["completeness"]`, `refs["validation"]` through `excerpt("validation_report", ...)`, the plan from `runs.get_run(conn, run_id).plan_ref`, `list_artifacts` for kinds, `refs["knowledge_index"]` as `KnowledgeIndex` for `passages`, the template through the tool); `precheck_context(ctx, deps) -> PrecheckContext`; `validate_review(draft: BaseModel, ctx: ChallengerContext, retrieved: Collection[str]) -> str | None` (the hook: unknown section id, alias outside `CHALLENGER_ALIASES`, path that fails `resolve_path` on the alias's excerpt (`validation_excerpt` or `plan.model_dump(mode="json")`), `[K:]` outside `retrieved`; message lists each bad entry); `validate_re_review(draft, expected_ids: set[str]) -> str | None` (exactly one verdict per expected id, no unknown id); `expand_evidence(evidence: list[str], ctx) -> list[str]` (`[A:validation#p]` -> `[A:{validation_id}#p]`, `[A:plan#p]` -> `[A:{plan_id}#p]`); `review(step, ctx, restricted: list[str]) -> list[FindingDraft]` (planning call with `ChallengeQueryPlan`, `search_regulation(k=3)` per query deduplicated in score order, review call with `validate_review`); `re_review(step, ctx, findings: list[Finding], changed: dict[str, str], restricted) -> dict[str, Verdict]` (skipped, returning `{}`, when no review finding is `accepted` or `disputed`); `reconcile(previous: list[Finding], prechecks: list[PrecheckFinding], verdicts: dict[str, Verdict], round: int, ctx) -> list[Finding]` (spec 3.4 rules; new pre-check findings get the next ids; closed findings get `closed_round`); `findings_summary(findings: list[Finding], round: int) -> dict[str, Any]` = `{"n_findings", "max_severity", "by_severity", "by_status", "by_source", "round"}`; `challenge(state, runtime) -> Command[Literal["remediate", "gate_signoff"]]` (spec 3.6 and section 5 replay rules; previous findings come from the `findings` artifact in `state.refs` when present; for a re-review the changed section ids are the union of `sections_regenerated` over the latest-round responses in the `responses` artifact and their bodies come from `split_sections` of the current `document_md`; the node stores the cumulative `findings` artifact, inserts new rows with `id = f"{run_id}/{finding.id}"`, calls `set_verdict` for every finding whose status or verdict changed, increments `counters.challenge_rounds`, adds `cost_usd`, and routes). Redaction list from `document.restricted_terms(deps, state)`. Finding ids `F-<n>` with `n` continuing from `len(previous)`; round-1 order: pre-check findings then review findings. The `prechecks` block sent to the model carries the pre-check findings as JSON (`PrecheckFinding` dumps).
- `nodes/__init__.py`: `invoke_node(node: Callable[..., Any], state: RunState, deps: Deps) -> tuple[dict[str, Any], str | None]`: calls `node(state, Runtime(context=deps))` (LangGraph's `Runtime` dataclass; the orchestrator may import `langgraph`) and returns `(update, goto)`, with `goto` `None` for a node that returns a plain dict; `modelforge_evals` drives nodes through it and never imports `Command` or `Runtime`.
- `conftest.py`: `recording_runtime(deps) -> tuple[Runtime[Deps], list[list[BaseMessage]]]`: a runtime whose gateway uses a `chat_model_factory` returning a `ScriptedChatModel` subclass that appends every `messages` list it receives to the captured list before answering; and `apply_node(state, result)` helper: `apply_update(state, result.update if isinstance(result, Command) else result)`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.db
def test_challenge_round_one_raises_prechecks_then_review_findings(validated_state, deps, runtime, db) -> None:
    state = apply_node(validated_state, document(validated_state, runtime))
    command = challenge(state, runtime)
    findings = deps.store.get_model(command.update["refs"]["findings"].id, FindingsList)
    ids = [f.id for f in findings.findings]
    assert ids == [f"F-{i}" for i in range(1, len(ids) + 1)] and findings.round == 1
    assert [f.source for f in findings.findings] == sorted([f.source for f in findings.findings], key=lambda s: s != "precheck")
    titles = {f.title for f in findings.findings}
    assert {"Monitoring thresholds are not quantified", "Approver identities are not named in the governance section"} <= titles
    assert all(f.status == "open" for f in findings.findings)
    monitoring = next(f for f in findings.findings if f.title.startswith("Monitoring"))
    with db.connection() as conn:
        plan_ref = runs.get_run(conn, state.run_id).plan_ref
    assert monitoring.evidence == ["monitoring", f"[A:{plan_ref}#thresholds.psi_warn]"]
    assert command.goto == "remediate" and command.update["counters"].challenge_rounds == 1
    assert command.update["refs"]["findings"].summary["by_status"] == {"open": len(ids)}
    with db.connection() as conn:
        rows = list_findings(conn, state.run_id)
    assert {r["id"] for r in rows} == {f"{state.run_id}/{i}" for i in ids}
    assert {c.purpose for c in list_llm_calls(conn, state.run_id)} >= {"challenge"}
```

Plus: `test_challenge_parks_on_refusal` (a `BaseChatModel` stub whose structured runnable answers the review request with `stop_reason == "refusal"` and no parsed object: `GatewayError.kind == "refusal"`, no `findings` rows); `test_challenge_routes_to_gate_when_bound_is_zero` (`state.max_remediation_rounds = 0` -> `goto == "gate_signoff"`); `test_review_hook_names_each_bad_evidence_entry` (`validate_review` on a draft with evidence `["nope", "[A:profile#n_rows]", "[A:validation#tests[zz].status]", "[K:k_unknown]"]` returns a message containing all four entries; a good draft returns `None`); `test_challenge_parks_after_persistent_bad_evidence` (a `BaseChatModel` stub answering every `ReviewDraft` request with evidence `["nope"]`: `pytest.raises(GatewayError)` with `kind == "schema"`, three `llm_calls` rows for the review prompt, no `findings` rows); `test_re_review_hook_requires_one_verdict_per_finding`; `test_re_review_closes_prechecks_deterministically_and_applies_verdicts` (call `reconcile` directly: a precheck finding whose key is absent from the new list closes with `closed_round == 2`; one still present and `accepted` becomes `open`; a review finding `accepted` with verdict `closed` closes; a new precheck key becomes `F-<next>` with `round == 2`); `test_challenge_replay_copies_findings_and_routes` (a replay state whose original manifest holds `findings` and `responses`: first call copies the artifact id and routes to `remediate`; with `counters.remediation_rounds == 1` routes to `gate_signoff`); `test_signoff_summary_shows_findings_by_status` moves to Task 9.

`test_challenger_isolation.py`:

```python
@pytest.mark.db
def test_developer_state_cannot_reach_the_challenger(db, store, deps, runtime, tmp_path) -> None:
    SENTINELS = {"brief": "SENTINEL-BRIEF-7f3a", "scope": "SENTINEL-SCOPE-9c1d", "draft": "SENTINEL-DRAFT-2b8e", "raw": "SENTINEL-RAW-5d4f", "dictionary": "SENTINEL-DICT-1a9b"}
    # brief: synthetic.yaml with constraints: "<SENTINEL-BRIEF>"; drive intake..validate, document
    # plant: scope_notes artifact re-stored with rationale containing SENTINEL-SCOPE (replace refs["scope_notes"]);
    #        a superseded document_sections artifact whose first section body and grounding.ungrounded_numbers carry SENTINEL-DRAFT (store it; keep refs["document_md"] rendered from the clean sections);
    #        an llm_response artifact with text SENTINEL-RAW recorded on the run; a column_dictionary artifact with a description SENTINEL-DICT (replace refs["dictionary"])
    recording, captured = recording_runtime(deps)
    challenge(state, recording)
    assert captured, "the challenger made no call"
    text = "\n".join(str(m.content) for messages in captured for m in messages)
    for name, sentinel in SENTINELS.items():
        assert sentinel not in text, name
    reads = store.read_log   # a RecordingStore wrapper installed by the test; kinds via list_artifacts
    allowed = {"document_md", "completeness", "validation_report", "modeling_plan", "knowledge_index", "brief", "column_dictionary"} | cited_ids(document_md)
    assert all(kind in allowed or artifact_id in allowed for artifact_id, kind in reads)
```

- [ ] **Step 2: Run** `uv run pytest packages/orchestrator/tests/test_challenger.py packages/orchestrator/tests/test_challenger_isolation.py -q`; expected: ImportError / AttributeError on the new names.
- [ ] **Step 3: Implement** `challenge.py`, the graph edge change, the conftest additions, delete the fixture file, trim `test_stubs.py`.
- [ ] **Step 4: Verify** `uv run pytest packages/orchestrator -q -m "not slow"` passes (the integration test still passes: the stub `remediate` follows the fixed edge to `gate_signoff`, and `assert_llm_outputs` is updated in Task 10) and `make lint` passes.
- [ ] **Step 5: Commit** `feat(orchestrator): challenger agent with lineage-only context, pre-checks, review, re-review and routing`.

---

### Task 8: Remediation prompts and the fake provider's remediation scripts

**Files:**
- Create: `prompts/remediate/system.v1.md`, `prompts/remediate/respond.v1.md`, `prompts/remediate/plan_queries.v1.md`, `prompts/remediate/rewrite_section.v1.md`
- Modify: `packages/gateway/modelforge_gateway/providers/fake.py`
- Test: `packages/gateway/tests/test_fake_provider.py`

**Interfaces:**
- Prompts (`purpose: remediate`): `system.v1` (`output_schema: FindingResponseDraft`): the model is the developer answering an independent reviewer; for each finding it either accepts (a concrete action; name the sections to regenerate when the fix is documentation) or disputes with artifact evidence markers by the path rules; never dismiss a finding without evidence; when rewriting a section, the document writing rules of `document/system.v1.md` apply verbatim (copy rules 1 to 9 into this file; prompt files are self-contained), plus: refer to a finding only by its title inside inline code (backticks) or not at all, never by its id in prose. `respond.v1`: blocks `finding`, `sections`, `excerpts`; task: return a `FindingResponseDraft`. `plan_queries.v1` (`output_schema: QueryPlan`): blocks `section`, `findings`; zero to three queries that help address the findings. `rewrite_section.v1` (`output_schema: SectionDraft`): the SP3 `write_section.v1.md` body with an added `findings` block ("the reviewer's findings this section must address; address each in the text") and the same optional `failures` block.
- Fake scripts: `FindingResponseDraft` -> for `finding.category == "governance"`: `{"decision": "dispute", "action": "Approvals are recorded in the lineage approvals table and the sealed manifest, not in the document body.", "evidence": ["[A:plan#model_name]"], "sections_to_regenerate": []}`; for `category == "data"` and `"prox" in title.lower()`: dispute with evidence `["[A:plan#target.event]"]` and action "The brief defines the target as a proxy for default and the limitations section states it."; otherwise `{"decision": "accept", "action": f"Revised the {', '.join(sections)} section(s) to address the finding.", "evidence": [], "sections_to_regenerate": [section ids found in finding.evidence that are keys of the sections block]}`. `SectionDraft` when the prompt carries a `findings` block: the SP3 section script, then for each finding one sentence: when some evidence entry matches `\[A:validation#tests\[(?P<id>[^\]]+)\]\.status\]` and the `validation` alias is in the excerpts: "The validation test `<id>` has outcome <format of resolve_path(excerpts['validation'], f'tests[{id}].status')> [A:validation#tests[<id>].status]."; otherwise "This revision addresses the reviewer's finding `<title with backticks replaced by '>`."; citations extended accordingly. `_infer_schema_name`: `finding` block -> `FindingResponseDraft`.

- [ ] **Step 1: Write the failing tests**: `test_fake_responds_by_category` (governance -> dispute with the plan marker; data proxy -> dispute; monitoring with evidence `["monitoring"]` -> accept with `sections_to_regenerate == ["monitoring"]`), `test_fake_rewrite_renders_cited_test_status_and_quotes_titles` (a `performance` section prompt with `findings` holding one precheck finding with evidence `[A:validation#tests[cal.hl].status]` and one review finding titled "AUC 0.70 breach": the body ends with the two sentences, the status sentence carries the marker, and the title sentence contains "`AUC 0.70 breach`"), `test_fake_rewrite_quotes_finding_titles_in_code_spans` (`grounding.numbers_in(grounding.strip_non_claims(last_sentence)) == []`).
- [ ] **Step 2: Run** `uv run pytest packages/gateway/tests/test_fake_provider.py -q`; expected: no script for `FindingResponseDraft`.
- [ ] **Step 3: Write the prompts and scripts.**
- [ ] **Step 4: Verify** `uv run pytest packages/gateway packages/knowledge/tests/test_wording.py -q` and `make lint` pass.
- [ ] **Step 5: Commit** `feat(gateway): remediation prompts and fake remediation scripts`.

---

### Task 9: The remediation node, the loop edges and the sign-off summary

**Files:**
- Modify: `packages/orchestrator/modelforge_orchestrator/nodes/remediate.py` (rewrite), `graph.py` (edges), `nodes/gates.py`, `packages/orchestrator/tests/conftest.py` (`signed_off_state` runs `document, challenge, remediate, challenge`)
- Delete: `packages/orchestrator/tests/test_stubs.py`
- Test: `packages/orchestrator/tests/test_remediation.py`, `test_loop.py`, `test_gates.py`

**Interfaces:**
- Consumes: Task 4 (`write_section(..., prompts=REMEDIATE_PROMPTS, findings_json=...)`, `render_and_check`, `restricted_terms`, `split_sections`), Task 7 (`challenge`, `expand_evidence` is not reused: the developer expands any alias through `for_section`'s aliases plus `plan`), Task 8, `set_response`.
- Produces: `REMEDIATE_PROMPTS = SectionPrompts(purpose="remediate", system="remediate/system", plan_queries="remediate/plan_queries", write="remediate/rewrite_section", version=1)`; `validate_response(draft: BaseModel, aliases: dict[str, Any], template_ids: set[str]) -> str | None` (dispute needs one resolvable `[A:alias#path]` over `aliases` (alias to excerpt), `sections_to_regenerate` subset of `template_ids`, accept needs non-empty `action`); `respond(step, deps, state, finding: Finding, sections: dict[str, str], template, restricted) -> FindingResponse` (one call; `sections` block limited to the finding's evidence section ids, all sections when none; `excerpts` block = union of `for_section` excerpts of those sections plus `plan`; evidence stored expanded to artifact ids); `regenerate(step, deps, state, template, section_ids: set[str], findings_by_section: dict[str, list[Finding]], restricted) -> DocumentSections` (writes each section with `write_section(..., prompts=REMEDIATE_PROMPTS, findings_json=json.dumps([...]))`, copies the other sections from the current `document_sections` artifact); `responses_summary(responses, round) -> dict` = `{"n_responses", "n_accepted", "n_disputed", "n_sections_regenerated", "round"}`; `remediate(state, runtime) -> Command[Literal["challenge"]]` (spec 4 and section 5: on replay copy `responses`, set rows, counter 1, no re-render; otherwise respond to every `open` finding, set rows, regenerate the accepted sections, `render_and_check`, store the cumulative `responses` artifact and an updated `findings` artifact with the new statuses and responses, increment `counters.remediation_rounds`, add `cost_usd`).
- `graph.py`: `_EDGES` loses `(remediate, gate_signoff)` and gains `(remediate, challenge)`; `challenge` and `remediate` are `Command` nodes like the gates.
- `gates.py`: `gate_summary("signoff")` adds `"findings": {**refs["findings"].summary, "rounds": state.counters.remediation_rounds}` and `"responses": refs["responses"].summary` when present.

- [ ] **Step 1: Write the failing tests**

`test_remediation.py`: `test_remediate_answers_every_open_finding_and_regenerates_named_sections` (after `document` and `challenge`: responses count equals open findings; the governance finding is `disputed` with expanded evidence `[A:<plan_id>#model_name]`; the monitoring finding `accepted` with `sections_regenerated == ["monitoring"]`; the new `document_sections` artifact differs from the old one only in the regenerated sections (compare bodies); `document_md`, `document_html`, `completeness` refs are new and `completeness.summary["complete"]`; `findings` artifact statuses updated; rows `status`/`response` set; `counters.remediation_rounds == 1`; `goto == "challenge"`; `llm_calls` purposes include `remediate`); `test_dispute_without_evidence_is_retried_then_parks` (a stub model answering disputes with `evidence: []`: `GatewayError.kind == "schema"`); `test_round_with_nothing_accepted_does_not_rerender` (a stub that disputes everything with `[A:plan#model_name]`: `document_md` ref unchanged); `test_remediate_replay_copies_responses_without_rerender`.

`test_loop.py`: `test_fake_loop_closes_remediated_findings_and_keeps_disputes` (drive `document -> challenge -> remediate -> challenge` with `apply_node`; after the second `challenge`: every finding is `closed` (with `closed_round == 2`) or `disputed`, the monitoring finding closed, the governance finding disputed, any `undiscussed_test:*` precheck finding closed, `goto == "gate_signoff"`, `challenge_rounds == 2`); `test_loop_exits_with_zero_rounds_and_when_everything_is_disputed` (`max_remediation_rounds=0` -> first `challenge` routes to the gate with findings open; a stub that disputes everything -> second `challenge` routes to the gate with everything `disputed`); `test_loop_stops_at_the_bound_with_findings_open` (a stub re-review that keeps everything `open`, `max_remediation_rounds=1`: after `challenge, remediate, challenge` the goto is `gate_signoff` with open findings).

`test_gates.py::test_signoff_summary_shows_findings_and_responses` (`gate_summary("signoff", signed_off_state, store)["findings"]["by_status"]` has no `open` key and `["responses"]["n_disputed"] == 1`).

- [ ] **Step 2: Run** the three files; expected: failures on the new names and routing.
- [ ] **Step 3: Implement**; update the fixture; delete `test_stubs.py`.
- [ ] **Step 4: Verify** `uv run pytest packages/orchestrator -q -m "not slow"` and `make lint` pass (the graph-based tests now run the loop under the fake).
- [ ] **Step 5: Commit** `feat(orchestrator): remediation loop with per-finding responses, section regeneration, re-render and re-review routing`.

---

### Task 10: Replay through the loop, CLI findings, end-to-end assertions

**Files:**
- Modify: `packages/orchestrator/modelforge_orchestrator/replay.py`, `cli.py`, `packages/orchestrator/tests/conftest.py` (`assert_llm_outputs`), `tests/test_cli.py`, `test_worker.py`, `test_seal.py`, `test_integration.py`, `test_polish_run.py`

**Interfaces:**
- `replay.py`: `LLM_DERIVED_REFS = ("document_md", "document_html", "completeness", "findings", "responses")`; `compare_refs(..., rerun_llm=True)` sets `regenerated = [name for name in LLM_DERIVED_REFS if name in original.refs]` and skips those names in the deterministic comparison (as today).
- `cli.py`: `status` prints `findings: <n> (open <a>, accepted <b>, disputed <c>, closed <d>; max severity <s>)` after the `cost:` line when the run has findings rows (counts from `list_findings`), and `--json` adds `"findings": [rows]`; the `replay --rerun-llm` help reads "Call the LLM again for scope, the document, the challenger and remediation instead of copying the original's outputs; the LLM-derived refs are then reported as regenerated."
- `assert_llm_outputs(status)`: purposes `== {"scope", "document", "challenge", "remediate"}`; `refs["findings"]["summary"]["n_findings"] >= 1`; `refs["responses"]["summary"]["n_responses"] >= 1`; `refs["findings"]["summary"]["by_status"]` has no `open` key; `len(status["findings"]) == refs["findings"]["summary"]["n_findings"]`.

- [ ] **Step 1: Write the failing tests**: `test_seal.py::test_compare_refs_rerun_llm_separates_regenerated` extended to expect the five names; `test_cli.py::test_cli_replay_rerun_llm_regenerates_and_records_calls` expects `regenerated == ["document_md", "document_html", "completeness", "findings", "responses"]` and purposes `challenge` and `remediate` among the rerun's `llm_calls`; `test_cli.py::test_cli_status_shows_findings_line_and_json_rows`; `test_cli.py::test_cli_signoff_approve_blocked_by_open_high_finding` (drive a synthetic run to `awaiting_signoff`, insert a high open finding row with `insert_findings` for that run id, `approve ... --decision approve` exits 2 with "approve is refused" and the title in stderr, then `approve_with_conditions --condition "close F-9"` exits 0 and the run completes); `test_worker.py::test_failure_in_challenge_after_document_resumes` (a tool failure injected in `challenge` parks the run; `resume` completes it: mirror `test_failure_in_first_node_after_a_gate_resumes`); `test_worker.py::test_replay_reproduces_every_deterministic_ref` now runs through the loop unchanged and must still report `ok` with `document_md`, `document_html` and `completeness` matched; `test_integration.py` and `test_polish_run.py` keep passing with the stricter `assert_llm_outputs` and additionally assert `status["refs"]["findings"]["summary"]["by_status"].get("disputed", 0) >= 1` (the governance dispute).
- [ ] **Step 2: Run** `uv run pytest packages/orchestrator/tests/test_seal.py packages/orchestrator/tests/test_cli.py packages/orchestrator/tests/test_worker.py -q`; expected: failures.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Verify** `MODELFORGE_REQUIRE_DB=1 uv run pytest packages/orchestrator -q`, with `MODELFORGE_DATA_DIR` pointing at the main checkout's `data/` directory (slow included, so the Polish run goes through the loop) and `make lint` pass. Record the Polish fake run's findings count, rounds and wall time in the commit body.
- [ ] **Step 5: Commit** `feat(orchestrator): replay --rerun-llm covers the challenger and remediation; status shows findings; sign-off policy end to end`.

---

### Task 11: Evals: configuration, retrieval goldens, metrics and the report model

**Files:**
- Create: `packages/evals/modelforge_evals/config.py`, `metrics.py`, `report.py`, `evals/evals.yml`, `evals/goldens/retrieval.yaml`
- Modify: `packages/knowledge/tests/test_retrieval.py` (`GOLDEN = REPO_ROOT / "evals/goldens/retrieval.yaml"`; the count assertion becomes `>= 8`)
- Delete: `packages/knowledge/tests/golden_questions.yaml`
- Test: `packages/evals/tests/test_config.py`, `test_metrics.py`, `test_report.py`, `packages/knowledge/tests/test_retrieval.py`

**Interfaces (produces):**
- `config.py`: `Layer = Literal["retrieval", "pipeline", "challenger", "cost"]`; `ApprovalSpec(StrictModel)`: `gate: GateName`, `decision: str`, `approver: str`; `ProfileEvalSpec(StrictModel)`: `brief: str`, `layers: list[Layer]`, `compare: Literal["exact", "tolerance"] = "tolerance"`, `ignore: list[str] = []`, `tolerances: dict[str, float] = {}`, `budgets: dict[str, float] = {}`; `EvalsConfig(StrictModel)`: `briefs: dict[str, Path]`, `approvals: list[ApprovalSpec]`, `profiles: dict[str, ProfileEvalSpec]`; `load_config(path: Path) -> EvalsConfig` (`EvalConfigError(ValueError)` on YAML or validation errors; brief paths resolved relative to the config file's parent's parent, the repo root). `evals/evals.yml` with the content of spec 10.2 (both live profiles spelled out).
- `evals/goldens/retrieval.yaml`: the eight SP3 questions plus at least twelve more in the same `question / doc_id / must_contain` form, covering every corpus document at least twice and written against the shipped corpus (verify each with `Retriever.search(k=5)` before committing; recall must be 1.0).
- `metrics.py`: `retrieval_recall_at_5(knowledge_dir: Path, goldens: Path) -> tuple[float, list[str]]` (recall and the missed questions); `RunMetrics(StrictModel)` flat fields: `pipeline_status: str`, `signoff_decision: str | None`, `n_sections: int`, `n_flagged_sections: int`, `grounding_pass_rate: float`, `n_ungrounded_numbers: int`, `completeness_complete: bool`, `scope_exact_match: bool`, `challenge_rounds: int`, `remediation_rounds: int`, `n_findings: int`, `findings_by_source: dict[str, int]`, `findings_by_severity: dict[str, int]`, `findings_by_status: dict[str, int]`, `n_sections_regenerated: int`, `cost_usd: float`, `cost_by_purpose: dict[str, float]`, `n_llm_calls: int`, `n_schema_retries: int`, `input_tokens: int`, `output_tokens: int`, `latency_ms_p50: float`, `latency_ms_p95: float`, `wall_time_s: float`, `wall_time_by_node_s: dict[str, float]`; `run_metrics(db: Database, store: ArtifactStore, run_id: str) -> RunMetrics` (refs from the sealed `Manifest`; `scope_exact_match` = identity fields of the manifest's `initial_plan_ref` plan equal the brief's and the `scope` purpose has exactly one `llm_calls` row; `n_schema_retries` = rows with `schema_valid == False`; percentiles by nearest rank over `latency_ms`; wall time from `steps.started_at/finished_at` per node, total = last finished minus first started); `flatten(metrics: RunMetrics) -> dict[str, float | int | bool | str | None]` (nested dicts flattened as `findings_by_status.closed`).
- `report.py`: `EvalReport(StrictModel)`: `profile: str`, `brief: str`, `run_id: str | None`, `models: dict[str, str]`, `orchestrator_version: str`, `prompts: dict[str, str]` (`name@version` to hash from `llm_calls`), `metrics: dict[str, float | int | bool | str | None]`, `defect_recall_by_class: dict[str, float | None]`, `probe_run_ids: list[str]`, `approvals: list[str]`, `misses: list[str]` (retrieval); `Regression(StrictModel)`: `metric: str`, `baseline: Any`, `value: Any`, `rule: str`; `compare(report: EvalReport, baseline: dict[str, Any], spec: ProfileEvalSpec) -> list[Regression]` (`exact`: every baseline metric not in `ignore` must equal; `tolerance`: for `name, tol` in `tolerances`, negative `tol` means `value >= baseline + tol`, positive means `value <= baseline + tol`; `budgets`: `value <= budget`); `baseline_of(report) -> dict[str, Any]` = `{"profile", "metrics"}`; `markdown_summary(report, regressions) -> str` (a table of metrics and a regressions list).

- [ ] **Step 1: Write the failing tests**: `test_config.py` (`load_config(REPO / "evals/evals.yml")` has profiles `fake`, `default`, `ci-cheap`; `fake.compare == "exact"`; a config missing `approvals` raises `EvalConfigError`); `test_metrics.py` (`retrieval_recall_at_5(REPO / "knowledge-base", REPO / "evals/goldens/retrieval.yaml") == (1.0, [])`; `flatten` of a `RunMetrics` with `findings_by_status={"closed": 2}` has key `findings_by_status.closed`; `@pytest.mark.db test_run_metrics_from_a_completed_fake_run` using a `completed_run_id`-style fixture in the evals conftest: `grounding_pass_rate == 1.0`, `n_findings >= 1`, `cost_usd == 0.0`, `n_llm_calls >= 24`, `remediation_rounds >= 1`); `test_report.py` (`compare` under `exact` flags a changed metric and ignores `wall_time_s`; under tolerances `grounding_pass_rate` falling by 0.2 with tolerance -0.1 is a regression and falling by 0.05 is not; `cost_usd` above the budget is a regression; `markdown_summary` contains the metric names).
- [ ] **Step 2: Run** `uv run pytest packages/evals packages/knowledge/tests/test_retrieval.py -q`; expected: ImportError / missing file.
- [ ] **Step 3: Implement** the three modules, the config, the goldens move and growth, the evals conftest (`settings`, `store`, `deps` and a `completed_run_id` fixture built with `modelforge_lineage.testing.database` and the orchestrator's `run_once` with `InProcessToolClient`, under `MODELFORGE_PROFILE=fake`; the fixture code mirrors the orchestrator conftest and lives in `packages/evals/tests/conftest.py`).
- [ ] **Step 4: Verify** `uv run pytest packages/evals packages/knowledge -q` and `make lint` pass.
- [ ] **Step 5: Commit** `feat(evals): evals.yml, retrieval goldens, run metrics and the eval report with baseline comparison`.

---

### Task 12: Evals: the harness, planted defects and probe runs

**Files:**
- Create: `packages/orchestrator/modelforge_orchestrator/submit.py`, `packages/evals/modelforge_evals/harness.py`, `defects.py`, `probes.py`, `evals/goldens/defects.yaml`
- Modify: `packages/orchestrator/modelforge_orchestrator/cli.py` (`run` uses `submit.create_run_from_brief`)
- Test: `packages/evals/tests/test_harness.py`, `test_defects.py`, `test_probes.py`, `packages/orchestrator/tests/test_cli.py` (unchanged assertions)

**Interfaces (produces):**
- `submit.py`: `create_run_from_brief(db: Database, store: ArtifactStore, brief_path: Path, *, requester: str, profile: str, cost_cap_usd: float) -> RunRecord` (the body of `cli.run` from `load_brief` to `enqueue(conn, run_id, "start")`, raising `BriefError`, `KeyError` (unknown adapter) or `GatewayError` instead of exiting; the CLI maps them to `_fail`).
- `harness.py`: `build_deps(settings: Settings, db: Database, store: ArtifactStore, profile: str) -> Deps` (a `StdioToolClient` and `Gateway.for_run`); `drive_run(settings, db, store, brief_path: Path, *, profile: str, approvals: list[ApprovalSpec], requester: str = "evals-requester") -> DrivenRun` where `DrivenRun(StrictModel)`: `run_id: str`, `status: str`, `approvals: list[str]` (the decisions actually recorded), `error: dict | None`; the loop: create, `run_once(..., run_id=...)`, then per approval `record_approval` (on `BlockingFindingsError` for `approve` at `signoff`, retry with `decision="approve_with_conditions"` and `conditions=[the error message's finding list]`), `enqueue(..., "resume")`, `run_once`; stops when the status is `needs_attention`, `completed` or `rejected`. `EvalError(RuntimeError)` for a run that parks.
- `defects.py`: `Mutation(StrictModel)`: `kind: Literal["remove_section", "swap_digit", "append_sentence", "swap_citation", "remove_test_sentences", "remove_sentences_containing"]`, `section: str`, `text: str | None = None`, `doc_id: str | None = None`, `statuses: list[str] = ["fail", "warn"]`; `Signature(StrictModel)`: `check_key: str | None = None` (fnmatch glob), `category: str | None = None`, `section: str | None = None`; `DefectCase(StrictModel)`: `id: str`, `mutation: Mutation`, `expect: Signature`; `load_defects(path) -> list[DefectCase]`; `MutationContext(StrictModel)`: `validation: dict[str, Any]` (the report JSON), `index: KnowledgeIndex`; `apply_mutation(markdown: str, template: TemplateSections, mutation: Mutation, ctx: MutationContext) -> str | None` (`None` when the mutation changes nothing: `remove_test_sentences` with no cited failing test, `remove_sentences_containing` with no match; `swap_digit` changes the last digit of the first number token in a sentence carrying an `[A:` marker, `9 -> 0`; `swap_citation` replaces the first `[K:<id>]` with the first chunk id of `doc_id` in `ctx.index`; `remove_section` drops from the heading through the character before the next `## ` heading; the markdown is rebuilt by replacing the section body in place so headings stay intact); `matches(finding: Finding, expect: Signature) -> bool`; `defect_metrics(cases: list[DefectCase], outcomes: dict[str, tuple[bool | None, list[Finding]]], baseline: list[Finding]) -> tuple[dict[str, float | None], float | None, float | None]` (recall per class with `None` for inapplicable cases, mean recall over applicable cases, precision as spec 10.4 with baseline findings keyed by `check_key` or `(category, title)`).
- `evals/goldens/defects.yaml`: the six cases of spec 10.4 in the `Mutation`/`Signature` fields (`undiscussed_breach` expects `check_key: "undiscussed_test:*"`; `miscited_regulation` expects `check_key: "miscited_regulation:regulatory_context:*"`).
- `probes.py`: `ProbeResult(StrictModel)`: `run_id: str`, `defect_id: str`, `applicable: bool`, `findings: list[Finding]`, `cost_usd: float`, `n_llm_calls: int`; `run_probe(deps: Deps, base_run_id: str, case: DefectCase, template: TemplateSections) -> ProbeResult` (spec 10.5: create the probe run row with `runs.create_run(requester="evals", ...)` copying the base run's profile, brief hash and ref, dataset ref, seed and cost cap; set status `running`; copy every artifact row of the base run onto the probe run with `steps.insert_artifact(producer_step_id=None)`; store and record the mutated `document_md`; recompute `completeness` through a `Step(deps, state, "probe")` calling `check_completeness`; `runs.set_plan_ref(probe, base.plan_ref)`; build the `RunState` with `max_remediation_rounds=0` and refs `document_md`, `completeness`, `validation`, `knowledge_index`; call `invoke_node(challenge, state, deps)` (Task 7) and read the findings from the update's `refs["findings"]`; append event `eval.probe {"base_run_id", "defect_id"}`; set status `completed`; return findings and cost from the probe's `llm_calls`); `run_probes(deps, base_run_id, cases) -> list[ProbeResult]`.

- [ ] **Step 1: Write the failing tests**: `test_defects.py` over the rendered fake document of a completed run (`completed_run_id` fixture): each of the six mutations changes the document and the challenger pre-checks or fake review raise a matching finding (`test_each_mutation_plants_a_detectable_defect`, parametrised by case id, calling `run_probe`); `test_inapplicable_defect_case_is_excluded_from_recall` (`apply_mutation` returns `None` for `remove_sentences_containing` with `text: "zzz-not-there"`; `defect_metrics` gives `None` for that class and the mean over the others); `test_matches_by_glob_and_by_category_section`; `test_precision_counts_unexpected_findings_only`. `test_harness.py`: `@pytest.mark.db test_drive_run_seals_the_synthetic_brief_under_fake` (status `completed`, three approvals, `run_metrics(...).n_findings >= 1`); `test_drive_run_falls_back_to_conditions_when_approve_is_blocked` (insert a high open finding before the sign-off approval: the recorded decision is `approve_with_conditions`). `test_probes.py`: `test_probe_run_is_isolated_and_records_calls` (the probe run id differs, its `llm_calls` carry purpose `challenge`, the base run's `cost_usd` is unchanged, the probe has the `eval.probe` event and status `completed`).
- [ ] **Step 2: Run** `uv run pytest packages/evals -q`; expected: ImportError.
- [ ] **Step 3: Implement**, including the `submit.py` extraction (the orchestrator CLI tests must still pass).
- [ ] **Step 4: Verify** `MODELFORGE_REQUIRE_DB=1 uv run pytest packages/evals packages/orchestrator/tests/test_cli.py -q` and `make lint` pass.
- [ ] **Step 5: Commit** `feat(evals): harness, planted-defect suite and challenger probe runs`.

---

### Task 13: Evals CLI, the fake baseline, Make targets and the CI workflows

**Files:**
- Create: `packages/evals/modelforge_evals/cli.py`, `evals/baselines/fake.json`, `.github/workflows/evals.yml`
- Modify: `Makefile`, `.github/workflows/ci.yml`
- Test: `packages/evals/tests/test_cli.py`

**Interfaces:**
- `cli.py` (Typer `app`): `run(profile: str, brief: str | None = None, layers: str | None = None, baseline: Path | None = None, write_baseline: Path | None = None, report_dir: Path = Path("evals/reports"), config: Path = Path("evals/evals.yml"), as_json: bool = False)`: loads settings with `load_settings()`, the config, runs the layers in the order `retrieval`, `pipeline`, `challenger` (probes on the pipeline run), `cost` (part of `run_metrics`; listed for the config's sake), builds the `EvalReport`, stores it as an `eval_report` artifact recorded on the pipeline run (`insert_artifact(producer_step_id=None)`), writes `<report_dir>/<profile>-<run_id>.json` (or `<profile>-retrieval.json` without a pipeline layer), prints the Markdown summary (and appends it to `$GITHUB_STEP_SUMMARY` when set), compares with `--baseline` and exits 1 listing regressions, writes `--write-baseline` from `baseline_of`; `compare(report: Path, baseline: Path, profile: str, config: Path = ...)` exits 1 on regressions. Exit code 2 on configuration errors and on a parked pipeline run (`EvalError`).
- `Makefile`: `evals: uv run modelforge-evals run --profile fake --baseline evals/baselines/fake.json`; `evals-live: uv run modelforge-evals run --profile $(PROFILE) --baseline evals/baselines/$(PROFILE).json` with `PROFILE ?= default`; `evals-baseline: uv run modelforge-evals run --profile fake --write-baseline evals/baselines/fake.json`.
- `ci.yml`: `- run: make evals` after `make test` (same env, Postgres service). `evals.yml`: `on: push: branches [main]`, `schedule: cron "0 3 * * *"`, `pull_request: types [labeled]` with `if: github.event_name != 'pull_request' || github.event.label.name == 'run-evals'`; the Postgres service as `ci.yml`; env `ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}`, `MODELFORGE_COST_CAP_USD: "8"`, `MODELFORGE_PROFILE: ci-cheap`; steps: checkout, setup-uv, `uv sync --all-groups`, the dataset cache, `uv run modelforge-evals run --profile ci-cheap --baseline evals/baselines/ci-cheap.json`, `upload-artifact` of `evals/reports/`.
- `evals/baselines/fake.json`: produced by `make evals-baseline` on this machine and committed.

- [ ] **Step 1: Write the failing tests** (`packages/evals/tests/test_cli.py`, subprocess `uv run modelforge-evals ...` against the test database like the orchestrator's `cli` fixture): `test_evals_run_fake_matches_committed_baseline` (exit 0; the report file exists; an `eval_report` artifact is recorded on the run; the summary names `grounding_pass_rate`); `test_evals_run_exits_1_on_regression` (a baseline copy with `n_findings` increased by 10 under `compare: exact` -> exit 1 and the metric named); `test_evals_run_retrieval_only_needs_no_database_run` (`--layers retrieval` writes `fake-retrieval.json`); `test_evals_compare_reports_regressions`.
- [ ] **Step 2: Run**; expected: no `modelforge_evals.cli`.
- [ ] **Step 3: Implement**, run `make evals-baseline`, commit the baseline, then `make evals` must exit 0.
- [ ] **Step 4: Verify** `MODELFORGE_REQUIRE_DB=1 uv run pytest packages/evals -q`, `make evals` and `make lint` pass; `uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/evals.yml'))"`.
- [ ] **Step 5: Commit** `feat(evals): modelforge-evals CLI, fake baseline, make targets and the evals workflow`.

---

### Task 14: README and end-to-end verification

**Files:**
- Modify: `README.md` (new sections "Challenger review", "Remediation loop", "Evals" after "Knowledge layer"; the "Commands" and "Configuration" sections gain `modelforge-evals`, `remediation_rounds`, the `findings:` status line; the results table gains "Findings (pre-check / review)", "Rounds" and "Sections regenerated" columns with the fake row filled and the live rows marked "pending" for the controller)

**Interfaces:** none new. The README text must say: the challenger sees only the rendered document, the validation report, the plan and the knowledge passages, and a test proves developer-side state cannot reach it; the six pre-checks in one sentence each; severities; the loop and its bound; the sign-off rule; that `fake` findings and responses are scripted; the evals layers as built, how to run `make evals` and `make evals-live PROFILE=default`, what the baselines are and how CI uses them; the SR 26-2 framing unchanged.

- [ ] **Step 1: Write the README sections.**
- [ ] **Step 2: Verify end to end**: `make lint`; `MODELFORGE_REQUIRE_DB=1 make test` with `MODELFORGE_DATA_DIR` pointing at the main checkout's `data/` directory (full suite, slow included); `make evals`; the manual Polish fake run through the CLI with the three `approve` commands, then `replay`, `replay --rerun-llm` and `verify`, pasting the `status` findings line and the replay `regenerated` list into the commit body.
- [ ] **Step 3: Commit** `docs(readme): challenger review, remediation loop and evals`.

---

## Controller closing work (after Task 14, not dispatched)

1. Live runs with the main checkout's `.env` (`ANTHROPIC_API_KEY`) made available to the worktree and `MODELFORGE_DATA_DIR` pointed at the main checkout: `make evals-live PROFILE=default` and `make evals-live PROFILE=ci-cheap` on the Polish brief (the harness drives the three gates; `approve` falls back to conditions when blocked); write `evals/baselines/default.json` and `ci-cheap.json` with `--write-baseline`; fill the README rows (findings by source, rounds, sections regenerated, cost, wall time, sign-off decision); record spend (authorised: about 6 USD).
2. Final whole-branch review on the most capable model; fix wave; scoped re-reviews.
3. `docs/superpowers/notes/2026-11-10-sp4-handoff.md` in the SP3 note's shape (verification, live runs, deliverables, SP5 recommendations, deferred minors, rulings); umbrella spec section 21 per spec section 14, plus the as-built amendments known from this plan: `check_key` instead of `check` (SQL reserved word), the fake's governance dispute finding (d), `submit.create_run_from_brief` shared by the CLI and the harness, `grounding.sentences` made public.
4. The finishing-a-development-branch menu.
