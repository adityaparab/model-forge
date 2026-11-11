# SP6 Documentation, Measurements and Presentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the repository into the artefact a reviewer opens: a front-door README with a scripted demo recording, measured hosted-versus-local results read from a committed results file, a design memo, a threat model, a domain primer, trade-off and cost tables, a repeatable fresh-clone test, and two small SP5-parked fixes.

**Architecture:** No new Python package. Three scripts under `scripts/` (a Playwright recorder, a results recorder that reads sealed runs through the eval harness and lineage, an offline docs checker), one shell script for the fresh-clone test, one manual GitHub workflow, Make targets, and Markdown under `docs/` with Mermaid diagrams and rendered results blocks between HTML-comment markers. The README's reference sections move to `docs/reference/` unchanged.

**Tech Stack:** Python 3.12 via `uv run` (stdlib plus `modelforge_lineage` and `modelforge_evals`), Node 22+ with `playwright@1.64.1` and `@mermaid-js/mermaid-cli@12.0.0` pinned in `scripts/package.json`, ffmpeg, Docker Compose, GitHub Actions, Markdown with Mermaid.

**Spec:** `docs/superpowers/specs/2026-11-11-sp6-docs-presentation-design.md` (sections cited as "spec N"). Umbrella: `docs/superpowers/specs/2026-11-09-modelforge-architecture-design.md`.

## Global Constraints

- Commit messages carry no attribution lines of any kind: no `Co-Authored-By`, no "Generated with" (spec 8).
- Employer-neutral tracked files: no company name, no `/Users/<name>/` or `/home/<name>/` path, no `.claude/worktrees/` path. The private pattern list lives only in the git-ignored `.neutrality-patterns` file or `MODELFORGE_NEUTRALITY_PATTERNS` (spec 6).
- Regulatory wording follows umbrella section 2 exactly: SR 26-2 (17 April 2026) superseded SR 11-7 and SR 21-8 and places generative and agentic AI outside its scope; never write that SR 26-2 extends SR 11-7 to LLMs or names LLM-as-judge or adversarial probing as validation techniques (spec 5).
- Every document states once that ModelForge is a reference implementation, not a production system (spec 5).
- Every number in `README.md` and `docs/trade-offs.md` comes from a results block rendered from `docs/results/runs.json`; no metric is typed by hand or passed on a command line (spec 3.2, 4.2).
- Pinned versions: `playwright@1.64.1` (Chromium 1248), `@mermaid-js/mermaid-cli@12.0.0`; exact versions in `scripts/package.json` and its lockfile (spec 3.1, 6).
- The README is at most 220 lines; `docs/media/demo.gif` under 8 MiB; `docs/media/demo.mp4` under 25 MiB (spec 2, 6).
- The reference carve-out moves text unchanged apart from the lead line, the back link and repointed relative links (spec 5.2).
- Tests and `ci.yml` stay offline; `demo.yml` is `workflow_dispatch` only (spec 3.3, 6).
- No live provider spend beyond the two runs already made (`run_d7e6c78b53b3` default, `run_378df0420b8c` ci-cheap); the local-ollama run is free (spec 4.1).
- Layering unchanged: `scripts/` is not a package and is not added to the import-linter contracts; `uv.lock` and `console/package.json` do not change (spec 2).
- Python style as the repo: ruff, ruff format, pyright standard, pytest `--import-mode=importlib` (test files under `scripts/tests` need unique module names and no `__init__.py`).
- Development database for measurements: the Compose Postgres on `localhost:5432` of the main checkout's stack, already migrated to 0005; the harness executes runs in-process, so the Compose `worker` container must be stopped while the local-ollama run is measured (controller's responsibility, stated in the Task 7 brief).

## Review Focus

1. A relative link in a moved reference page that pointed at a README anchor now points nowhere: `check_docs.py` must fail on an unresolved link or anchor. Test in Task 2 (`test_broken_link_fails`, `test_missing_anchor_fails`).
2. `render --check` reports a stale block when only line endings or a trailing newline differ, so CI reddens on a byte quirk: the renderer normalises to `\n` and one trailing newline before comparing. Test in Task 3 (`test_render_check_ignores_trailing_newline`).
3. A results block with zero records renders an empty table while the prose claims measurements: `table` with no records prints a single row "no records yet" and `check_docs.py` fails when a results block in `README.md` has no data rows. Tests in Task 3 (`test_table_without_records`) and Task 2 (`test_empty_results_block_fails`).
4. `fresh_clone_test.sh` when something already answers on port 8000 or the clone's `make demo` never reaches the gate: refuse before cloning, and time out with the API log printed. Test in Task 5 (`test_fresh_clone_refuses_busy_port` with a dry-run harness).
5. `mmdc` missing or failing to launch its browser must fail the Mermaid check loudly, never pass by skipping. Test in Task 2 (`test_mermaid_check_fails_when_mmdc_missing`).

---

## File Structure

- Create: `scripts/package.json`, `scripts/package-lock.json`, `scripts/demo_record.mjs`, `scripts/record_result.py`, `scripts/check_docs.py`, `scripts/fresh_clone_test.sh`, `scripts/tests/test_record_result.py`, `scripts/tests/test_check_docs.py`, `scripts/tests/test_fresh_clone_test.py`, `scripts/tests/fixtures/` (a report JSON, a run fixture, a tiny docs tree).
- Create: `docs/index.md`, `docs/design-memo.md`, `docs/threat-model.md`, `docs/domain-primer.md`, `docs/trade-offs.md`, `docs/walkthrough.md`, `docs/results/runs.json`, `docs/results/README.md`, `docs/media/demo.mp4`, `docs/media/demo.gif`, `docs/media/shots/NN-<step>.png`, `docs/reference/{run,console,api,tracing,mcp-servers,gateway,knowledge,challenger,remediation,evals,live-models,compose,configuration,commands,datasets}.md`.
- Create: `.github/workflows/demo.yml`.
- Modify: `README.md`, `Makefile`, `.gitignore`, `.dockerignore`, `pyproject.toml` (pytest `testpaths`), `.github/workflows/ci.yml`, `evals/evals.yml`, `packages/api/modelforge_api/routers/writes.py`, `packages/orchestrator/modelforge_orchestrator/cli.py`, `packages/api/modelforge_api/schemas.py`, `packages/api/modelforge_api/queries.py`, `console/src/components/run/ReplaysPanel.tsx`, `console/openapi.json`, `console/src/api/types.gen.ts`, `console/src/fixtures/*`, `docs/superpowers/specs/2026-11-10-sp5-api-console-design.md` (section 13), `docs/superpowers/notes/2026-11-10-sp5-handoff.md` (parked lines), `docs/superpowers/specs/2026-11-09-modelforge-architecture-design.md` (section 23), `docs/superpowers/specs/2026-11-11-sp6-docs-presentation-design.md` (section 10).

---

### Task 1: Docs tooling scaffold

**Files:**
- Create: `scripts/package.json`, `scripts/package-lock.json`, `scripts/tests/fixtures/.gitkeep`
- Modify: `pyproject.toml` (pytest `testpaths`), `.gitignore`, `.dockerignore`, `Makefile`

**Interfaces:**
- Produces: `npm ci --prefix scripts` installs `playwright@1.64.1` and `@mermaid-js/mermaid-cli@12.0.0` (exact, no caret); `scripts/tests` is a pytest test path; `make scripts-install` runs `npm ci --prefix scripts`.

- [ ] **Step 1: Write `scripts/package.json`**

```json
{
  "name": "modelforge-docs-tooling", "private": true, "version": "0.0.0", "type": "module",
  "description": "Pinned tooling for the demo recording and the docs checks; not part of the console.",
  "devDependencies": {"playwright": "1.64.1", "@mermaid-js/mermaid-cli": "12.0.0"}
}
```

- [ ] **Step 2: Install and commit the lockfile**

Run: `npm install --prefix scripts --package-lock-only && npm ci --prefix scripts`
Expected: `scripts/package-lock.json` written; `scripts/node_modules/.bin/mmdc` and `.../playwright` exist.

- [ ] **Step 3: Ignore and exclude**

Add to `.gitignore`: `scripts/node_modules/`, `.neutrality-patterns`, `docs/media/*.webm`. Add to `.dockerignore`: `scripts/`, `docs/media/`, `docs/superpowers/`.

- [ ] **Step 4: pytest test path and Make target**

In `pyproject.toml` append `"scripts/tests"` to `testpaths`. In `Makefile` add `scripts-install: ; npm ci --prefix scripts`.

- [ ] **Step 5: Verify collection still works**

Run: `uv run pytest --collect-only -q | tail -1`
Expected: the same count as before (645) with no error about `scripts/tests`.

- [ ] **Step 6: Commit**

```bash
git add scripts/package.json scripts/package-lock.json scripts/tests/fixtures/.gitkeep pyproject.toml .gitignore .dockerignore Makefile
git commit -m "chore(scripts): pinned docs tooling (playwright 1.64.1, mermaid-cli 12.0.0), scripts/tests path"
```

---

### Task 2: `scripts/check_docs.py` and the CI docs job

**Files:**
- Create: `scripts/check_docs.py`, `scripts/tests/test_check_docs.py`, `scripts/tests/fixtures/docs_ok/` (a `README.md` with one valid link, one anchor link, one results block with a data row, one Mermaid block; `docs/a.md`; `docs/media/demo.gif` 1 KB), `scripts/tests/fixtures/docs_bad/` (variants per test)
- Modify: `Makefile` (`docs-check`), `.github/workflows/ci.yml` (job `docs`)

**Interfaces:**
- Produces: `uv run python scripts/check_docs.py [--root DIR] [--no-mermaid] [--patterns FILE]` exits 0 when every check passes and 1 with one `check: <name>: <path>: <reason>` line per failure. Functions for the tests: `find_links(md_text) -> list[Link]` (`Link(path, anchor, line)`), `check_links(root) -> list[Failure]`, `check_sizes(root, limits) -> list[Failure]`, `check_neutrality(root, patterns) -> list[Failure]`, `check_readme_length(root, max_lines=220) -> list[Failure]`, `check_results_blocks(root) -> list[Failure]`, `check_mermaid(root, mmdc=Path) -> list[Failure]`, `load_patterns(root) -> list[re.Pattern]` (generic list plus `.neutrality-patterns` lines plus `MODELFORGE_NEUTRALITY_PATTERNS` split on `;`), `main(argv) -> int`. `Failure(check: str, path: str, reason: str)`.

Generic neutrality patterns (exact): `r"/Users/[A-Za-z0-9._-]+/"`, `r"/home/[A-Za-z0-9._-]+/"`, `r"\.claude/worktrees/"`, `r"postgresql://[^\s]+:[^\s]+@"` with the allowlist of files `docker-compose.yml`, `.env.example`, `README.md`, `docs/reference/configuration.md`, `docs/reference/compose.md`, `docs/reference/run.md`, `packages/orchestrator/modelforge_orchestrator/config.py`, `.github/workflows/*.yml`, and the test fixtures under `scripts/tests/fixtures/`. Tracked files come from `git ls-files` in `root`.

Links: `[text](path)`, `[text](path#anchor)`, `![alt](path)`, in `README.md` and `docs/**/*.md`; `http(s)://` and `mailto:` links are skipped; an anchor must match a heading in the target (GitHub slug: lowercase, spaces to `-`, punctuation other than `-` and `_` dropped). Results blocks: `<!-- results:NAME -->` ... `<!-- /results:NAME -->` in `README.md` must contain a Markdown table with at least one data row. Mermaid: every ```` ```mermaid ```` block is written to a temporary `.mmd` and rendered with `scripts/node_modules/.bin/mmdc -i X -o Y.svg -q`; a missing `mmdc` is a failure `mermaid: <file>: mmdc not installed (run make scripts-install)` unless `--no-mermaid`.

- [ ] **Step 1: Write the failing tests**

`scripts/tests/test_check_docs.py` (fixtures copied into `tmp_path` and modified per test):

```python
def test_ok_tree_passes(tmp_docs_ok): assert check_docs.main(["--root", str(tmp_docs_ok), "--no-mermaid"]) == 0
def test_broken_link_fails(tmp_docs_ok):  # README links docs/missing.md
    failures = check_docs.check_links(tmp_docs_ok); assert failures[0].check == "links" and "docs/missing.md" in failures[0].reason
def test_missing_anchor_fails(tmp_docs_ok):  # [x](docs/a.md#not-a-heading)
    assert any(f.reason.endswith("#not-a-heading") for f in check_docs.check_links(tmp_docs_ok))
def test_oversized_gif_fails(tmp_docs_ok):  # write 8 MB + 1 byte
    assert check_docs.check_sizes(tmp_docs_ok, {"docs/media/demo.gif": 8 * 1024 * 1024})[0].check == "sizes"
def test_neutrality_hit_fails(tmp_docs_ok, monkeypatch):
    monkeypatch.setenv("MODELFORGE_NEUTRALITY_PATTERNS", "Acme Bank;zephyr-box")
    (tmp_docs_ok / "docs" / "a.md").write_text("built at Acme Bank\n")
    assert check_docs.check_neutrality(tmp_docs_ok, check_docs.load_patterns(tmp_docs_ok))[0].reason == "docs/a.md:1 matches 'Acme Bank'"
def test_generic_home_path_fails(tmp_docs_ok):  # "/Users/someone/x" in docs/a.md
def test_readme_over_220_lines_fails(tmp_docs_ok):
def test_empty_results_block_fails(tmp_docs_ok):  # block with header row only
def test_mermaid_check_fails_when_mmdc_missing(tmp_docs_ok):
    assert check_docs.check_mermaid(tmp_docs_ok, mmdc=tmp_docs_ok / "nope")[0].reason.startswith("mmdc not installed")
def test_mermaid_block_renders(tmp_docs_ok):  # skipped unless scripts/node_modules/.bin/mmdc exists
```

The fixtures are real tracked files inside a temporary git repository (`git init`, `git add -A` in the fixture) because the checker lists tracked files with `git ls-files`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest scripts/tests/test_check_docs.py -q`
Expected: FAIL with `ModuleNotFoundError: check_docs` (the tests import it by adding `scripts/` to `sys.path` in a `conftest.py` under `scripts/tests`).

- [ ] **Step 3: Implement `scripts/check_docs.py`**

Pure stdlib (`re`, `pathlib`, `subprocess`, `argparse`, `os`, `tempfile`). The heading slugger follows GitHub's rule above; `check_links` reads `git -C root ls-files` once and only follows links into tracked files.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest scripts/tests/test_check_docs.py -q && uv run ruff check scripts && uv run pyright scripts/check_docs.py`
Expected: all PASS (the mmdc render test runs because Task 1 installed it); lint clean.

- [ ] **Step 5: Make target and CI job**

`Makefile`: `docs-check: ; uv run python scripts/check_docs.py`. In `.github/workflows/ci.yml` add a job `docs` (`needs: lint-test`, `ubuntu-latest`) with steps: checkout, setup-uv, `uv python install 3.12`, `uv sync --all-groups`, setup-node 22, `npm ci --prefix scripts`, `make docs-check`. (Task 3 adds `make results-check` to it.) Run `make docs-check` locally against the real tree; it must pass on the current README (no results blocks yet, so the results-block check applies only when blocks exist).

- [ ] **Step 6: Commit**

```bash
git add scripts/check_docs.py scripts/tests Makefile .github/workflows/ci.yml
git commit -m "feat(scripts): offline docs checks (links, anchors, sizes, neutrality, README length, results blocks, mermaid) and a CI docs job"
```

---

### Task 3: `scripts/record_result.py`, `docs/results/`

**Files:**
- Create: `scripts/record_result.py`, `scripts/tests/test_record_result.py`, `scripts/tests/fixtures/report-fake.json` (a copy of `evals/reports/fake-run_b4b2b2f8e6d4.json` with the run id kept), `docs/results/runs.json` (`[]`), `docs/results/README.md`
- Modify: `Makefile` (`results`, `results-check`), `.github/workflows/ci.yml` (`make results-check` in the `docs` job)

**Interfaces:**
- Consumes: `modelforge-evals run --profile P --run-id R --layers pipeline,retrieval` writes `evals/reports/<P>-<R>.json` with keys `profile, brief, run_id, models, orchestrator_version, prompts, metrics, defect_recall_by_class, probe_run_ids, approvals, misses` and flattened `metrics` (`grounding_pass_rate`, `completeness_complete`, `n_flagged_sections`, `n_ungrounded_numbers`, `n_sections`, `n_sections_regenerated`, `n_findings`, `findings_by_source.*`, `findings_by_severity.*`, `findings_by_status.*`, `challenge_rounds`, `remediation_rounds`, `scope_exact_match`, `retrieval_recall_at_5`, `n_llm_calls`, `n_schema_retries`, `input_tokens`, `output_tokens`, `cost_usd`, `cost_by_purpose.*`, `wall_time_s`, `wall_time_by_node_s.*`, `signoff_decision`, `pipeline_status`); `modelforge_lineage.runs.get_run`, `runs.list_replays`, `modelforge_lineage.approvals.list_approvals`, `modelforge_orchestrator.config.load_settings` for the database URL.
- Produces: `record_result.py add RUN_ID --profile P --label L [--report PATH] [--note TEXT]`, `record_result.py table parity|cost|runs [--results PATH]`, `record_result.py render FILE... [--results PATH] [--check]`; functions `build_record(report: dict, run, approvals, replays, *, label, note) -> dict`, `load_records(path) -> list[dict]`, `save_records(path, records)`, `table(name, records) -> str`, `render(text, records) -> str`, `main(argv) -> int`. Record keys exactly as spec 4.2; `cost_usd` and `cost_by_purpose` for a profile without prices stay `0.0` and the parity table prints `0 (local)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_build_record_from_report(fixture_report, fake_run, fake_approvals, fake_replays):
    rec = record_result.build_record(fixture_report, fake_run, fake_approvals, fake_replays, label="hosted-default", note=None)
    assert rec["run_id"] == fixture_report["run_id"] and rec["label"] == "hosted-default"
    assert rec["findings"] == {"total": 5, "precheck": 5, "review": 0, "high": 1, "medium": 3, "low": 1, "open_at_signoff": 0}  # values from the fixture
    assert rec["rounds"] == {"challenge": 2, "remediation": 1} and rec["replay_ok"] is True
    assert set(rec) == RECORD_KEYS
def test_save_is_sorted_and_stable(tmp_path): ...  # sorted keys, trailing newline, replace on same run_id
def test_table_parity_has_one_row_per_record(records):
    rows = [l for l in record_result.table("parity", records).splitlines() if l.startswith("| ") and not l.startswith("| ---")]
    assert len(rows) == len(records) + 1  # the header row plus one row per record
def test_table_without_records(): assert "no records yet" in record_result.table("parity", [])
def test_local_cost_prints_as_local(records_with_local): assert "0 (local)" in record_result.table("parity", records_with_local)
def test_render_replaces_only_between_markers(): ...
def test_render_check_ignores_trailing_newline(): ...  # text with "\r\n" and no final newline compares equal
def test_render_check_detects_stale_block(): ...
def test_add_runs_harness_when_no_report(monkeypatch, tmp_path): ...  # subprocess.run patched; asserts the exact argv
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest scripts/tests/test_record_result.py -q`
Expected: FAIL (`record_result` missing).

- [ ] **Step 3: Implement `scripts/record_result.py`**

The parity row columns (in order): Profile (`label`), Model (the `document` purpose's model), Status (`status` and `signoff`), Grounding (`grounding_pass_rate` as a percentage with one decimal), Complete (`yes`/`no` with `n_flagged_sections`), Ungrounded numbers, Findings (`precheck`/`review`; `high`/`medium`/`low`), Rounds (`challenge`/`remediation`), Sections regenerated, Schema retries, LLM calls, Cost USD (two decimals or `0 (local)`), Wall time (`m:ss`). Cost columns: Profile, Input tokens, Output tokens, scope, document, challenge, remediate, Total, Cap. Runs columns: Run id, Label, Recorded (date), Orchestrator version, Replay ok. The models map comes from the report's `models`; `status`, `signoff` from the run and the last approval; `replay_ok` from the latest completed plain replay's report `ok`; `cost_cap_usd` from the run.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest scripts/tests -q && uv run ruff check scripts && uv run pyright scripts/record_result.py`
Expected: PASS; lint clean.

- [ ] **Step 5: Results directory, Make targets, CI step**

`docs/results/runs.json` = `[]\n`. `docs/results/README.md`: how a record is produced (`uv run python scripts/record_result.py add <run_id> --profile <p> --label <l>`), what each field means, and a `<!-- results:runs -->` block. `Makefile`: `results: ; uv run python scripts/record_result.py render README.md docs/trade-offs.md docs/results/README.md` and `results-check: ; uv run python scripts/record_result.py render --check README.md docs/trade-offs.md docs/results/README.md` (files that do not exist yet are skipped with a notice). Add `make results-check` to the CI `docs` job after `make docs-check`.

- [ ] **Step 6: Commit**

```bash
git add scripts/record_result.py scripts/tests docs/results Makefile .github/workflows/ci.yml
git commit -m "feat(scripts): record sealed runs into docs/results/runs.json and render results tables into docs"
```

---

### Task 4: Hardening: abort lock order and missing replay reports

**Files:**
- Modify: `packages/api/modelforge_api/routers/writes.py` (`abort`), `packages/orchestrator/modelforge_orchestrator/cli.py` (`abort`), `packages/api/modelforge_api/schemas.py` (`ReplayItem`), `packages/api/modelforge_api/queries.py` (`replay_items`), `console/src/components/run/ReplaysPanel.tsx`, `console/openapi.json`, `console/src/api/types.gen.ts`, `console/src/fixtures/*` (regenerated), `docs/superpowers/specs/2026-11-10-sp5-api-console-design.md` (section 13), `docs/superpowers/notes/2026-11-10-sp5-handoff.md`
- Test: `packages/api/tests/test_writes.py`, `packages/orchestrator/tests/test_cli.py`, `packages/api/tests/test_runs_read.py`, `console/src/components/run/ReplaysPanel.test.tsx`

**Interfaces:**
- Produces: `ReplayItem` gains `report_missing: bool = False` ("the replay sealed but its report artifact is not in the store"); `replay_items` never raises for a missing report; `abort` (API and CLI) calls `runs.transition(...)` first and `release(conn, run_id)` only after a successful transition.

- [ ] **Step 1: Write the failing tests**

`test_writes.py::test_abort_locks_the_run_before_releasing_the_queue`: with a run `awaiting_model` and a queued resume row, hold the run row `for update` in one connection, post the abort from a thread, and while the lock is held assert the queue row is still present (the abort has not released it before the transition); release the lock after 0.3 s; the abort succeeds (200, `aborted`) and the queue row is gone. `test_cli.py::test_abort_transitions_before_release`: same shape through the CLI with `CliRunner`. `test_runs_read.py::test_missing_replay_report_is_flagged_not_404`: delete the replay report artifact file; `GET /runs/{id}/replays` is 200 with `report: null, report_missing: true`, and `GET /runs/{id}` is 200. `ReplaysPanel.test.tsx` "a replay with a missing report shows report missing": the row's status cell reads `report missing` and the badge is not "Reproduced".

- [ ] **Step 2: Run them to verify they fail**

Run: `MODELFORGE_REQUIRE_DB=1 uv run pytest packages/api/tests/test_writes.py packages/api/tests/test_runs_read.py packages/orchestrator/tests/test_cli.py -q -k "abort_locks or transitions_before or missing_replay" && npm test --prefix console -- ReplaysPanel`
Expected: FAIL (release before transition; 404; no `report_missing`).

- [ ] **Step 3: Implement**

`abort` (both): inside the transaction, `if not runs.transition(...)`: read the status and refuse as today; else `release(conn, run_id)`. `replay_items`: catch `ArtifactNotFound`, log one warning, append `ReplayItem(run=replay, report=None, report_missing=True)`. Console: `ReplaysPanel` status cell shows `report missing` when `report_missing`, and `reproduced()` requires `report?.ok` (unchanged) so such a row never wins the badge. Regenerate: `make console-types && make console-fixtures` (fixtures only if the shape changed: `report_missing: false` appears in `replays.json`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: the Step 2 command, then `make lint`
Expected: PASS; lint clean; `make console-types` leaves no diff.

- [ ] **Step 5: Amend the SP5 documents**

SP5 spec section 13: replace the sentence "a manifest or replay report missing from the store is `404` kind `not_found` (for a replay report, on `GET /runs/{id}` as well, since the detail lists the replays)" with "a manifest missing from the store is `404` kind `not_found`; a replay whose report is missing is listed with `report: null` and `report_missing: true` (SP6)". SP5 hand-off: under the parked items, append "(closed in SP6)" to the abort lock-order and missing-report lines.

- [ ] **Step 6: Commit**

```bash
git add packages/api packages/orchestrator console docs/superpowers/specs/2026-11-10-sp5-api-console-design.md docs/superpowers/notes/2026-11-10-sp5-handoff.md
git commit -m "fix: abort locks the run before releasing its queue row; a missing replay report is flagged, not a 404"
```

---

### Task 5: Fresh-clone test script and the manual demo workflow

**Files:**
- Create: `scripts/fresh_clone_test.sh`, `scripts/tests/test_fresh_clone_test.py`, `.github/workflows/demo.yml`
- Modify: `Makefile` (`fresh-clone-test`, `MODELFORGE_DEMO_NO_OPEN` guard in `demo`)

**Interfaces:**
- Produces: `scripts/fresh_clone_test.sh [REMOTE_URL] [--dry-run]` exits 0 printing `fresh-clone: awaiting_scope after <N> s`; env `MODELFORGE_FRESH_PORT` (default 5434), `MODELFORGE_FRESH_TIMEOUT_S` (default 1200). `--dry-run` prints each command it would run, one per line, and runs none (it still checks port 8000). `make demo` skips the `open` step when `MODELFORGE_DEMO_NO_OPEN` is set.

- [ ] **Step 1: Write the failing tests**

`test_fresh_clone_test.py`: `test_dry_run_lists_the_steps` (asserts the printed lines contain `git clone --depth 1`, `COMPOSE_PROJECT_NAME=modelforge-fresh`, `MODELFORGE_POSTGRES_PORT=5434`, `make demo`, `docker compose down -v`, in that order); `test_fresh_clone_refuses_busy_port` (bind a socket on 8000 if free, else skip; the script exits 3 with `fresh-clone: something already answers on port 8000`); `test_bash_syntax` (`bash -n`).

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest scripts/tests/test_fresh_clone_test.py -q`
Expected: FAIL (no script).

- [ ] **Step 3: Implement the script, the Make target, the guard**

`set -euo pipefail`; `trap cleanup EXIT` that runs `docker compose down -v` in the clone and `rm -rf` the temp dir; the port check with `nc -z localhost 8000` or `curl -s`; `MODELFORGE_DEMO_NO_OPEN=1 COMPOSE_PROJECT_NAME=modelforge-fresh MODELFORGE_POSTGRES_PORT=$PORT make demo`; poll `GET /api/v1/runs/<id>` for `awaiting_scope` every 5 s until the timeout, on timeout print `docker compose logs --tail 50 api worker` and exit 4. Makefile: `fresh-clone-test: ; bash scripts/fresh_clone_test.sh` and the `demo` recipe's open line guarded by `[ -n "$$MODELFORGE_DEMO_NO_OPEN" ] ||`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest scripts/tests/test_fresh_clone_test.py -q`
Expected: PASS.

- [ ] **Step 5: Write `.github/workflows/demo.yml`**

`name: demo`; `on: workflow_dispatch`; one job `fresh-clone` on `ubuntu-latest`, `timeout-minutes: 30`; steps: checkout (needed only for the script), `bash scripts/fresh_clone_test.sh https://github.com/${{ github.repository }}.git`, and `if: failure()` an `actions/upload-artifact@v4` of `/tmp/fresh-clone-logs/*.log` which the script writes on failure.

- [ ] **Step 6: Commit**

```bash
git add scripts/fresh_clone_test.sh scripts/tests/test_fresh_clone_test.py .github/workflows/demo.yml Makefile
git commit -m "feat: fresh-clone test of make demo as a script, a make target and a manual workflow"
```

---

### Task 6: `scripts/demo_record.mjs`, the recording and the screenshots

**Files:**
- Create: `scripts/demo_record.mjs`, `docs/media/demo.mp4`, `docs/media/demo.gif`, `docs/media/shots/01-runs.png` … `13-sealed.png`
- Modify: `Makefile` (`demo-record`)

**Interfaces:**
- Consumes: the console routes `/`, `/runs/new`, `/runs/<id>`, `/runs/<id>/document|findings|lineage`, `/costs`; the identity textbox (placeholder `your name`); the decision radios labelled `approve`, `accept`, `reject`, `adjust`; the submit button `Record decision: <decision>`; the four-eyes refusal text under the form; the Replays badge text `Reproduced: N artifacts matched`.
- Produces: `node scripts/demo_record.mjs --base URL --out DIR [--brief PATH] [--no-video]`; `make demo-record`.

- [ ] **Step 1: Write the script**

Steps and file names exactly as spec 3.1 (13 steps). Each step is `await step("03-scope-gate", async () => {...})` which waits on a text locator with a 120 s timeout, then screenshots to `shots/`. Identity changes type into the textbox and press Tab. The four-eyes step records the refusal text as a screenshot and continues. After `context.close()`, the WebM path is read from `page.video().path()` and ffmpeg runs: `ffmpeg -y -i in.webm -vf "scale=1440:-2,format=yuv420p" -c:v libx264 -crf 23 -movflags +faststart demo.mp4` and the two-pass GIF: `ffmpeg -y -i in.webm -vf "fps=10,scale=720:-1:flags=lanczos,palettegen" palette.png` then `ffmpeg -y -i in.webm -i palette.png -lavfi "fps=10,scale=720:-1:flags=lanczos [x]; [x][1:v] paletteuse" demo.gif`. Sizes are checked and the script exits 2 over the limits, printing both sizes.

- [ ] **Step 2: Make target**

`demo-record:` starts `COMPOSE_PROJECT_NAME=modelforge-record MODELFORGE_POSTGRES_PORT=5433 docker compose up -d --build --wait`, waits for health, runs `node scripts/demo_record.mjs --out docs/media`, and tears down with `down -v` in all cases (a shell `trap`). The brief is the Polish one; the profile is `fake` (the stack's default).

- [ ] **Step 3: Record**

Run: `make demo-record`
Expected: 13 screenshots, `demo.mp4` and `demo.gif` written with their sizes printed under the limits; the GIF opens and shows the run from submit to sealed.

- [ ] **Step 4: Check the media and commit**

Run: `make docs-check` (sizes) and `ls -la docs/media docs/media/shots`.

```bash
git add scripts/demo_record.mjs Makefile docs/media
git commit -m "feat(docs): scripted console recording (MP4, GIF, screenshots) via Playwright"
```

---

### Task 7: Measurements: evals profile, local-ollama run, three records

**Files:**
- Modify: `evals/evals.yml` (profile `local-ollama`), `docs/results/runs.json`, `docs/results/README.md`
- Create: `evals/baselines/local-ollama.json`

**Interfaces:**
- Consumes: Task 3's `record_result.py`; the development database with the sealed runs `run_d7e6c78b53b3` (default) and `run_378df0420b8c` (ci-cheap); Ollama at `http://localhost:11434` serving `gpt-oss:20b`; the Compose `worker` container stopped by the controller before this task starts.
- Produces: three records in `docs/results/runs.json` labelled `hosted-default`, `hosted-ci-cheap`, `local-ollama`.

- [ ] **Step 1: Add the profile to `evals/evals.yml`**

```yaml
  local-ollama:
    brief: polish
    layers: [retrieval, pipeline, cost]
    tolerances: {grounding_pass_rate: -0.20, retrieval_recall_at_5: -0.05}
    budgets: {cost_usd: 0.0, wall_time_s: 7200}
```

The challenger probe layer is excluded for the local profile because its probe runs would multiply the local wall time; the pipeline's own challenger rounds are still measured.

- [ ] **Step 2: Run the local measurement**

Run (from the repository root, Compose worker stopped): `uv run modelforge-evals run --profile local-ollama --write-baseline evals/baselines/local-ollama.json 2>&1 | tee /tmp/local-ollama-eval.log`
Expected: exit 0 with the metrics table and `evals/baselines/local-ollama.json` written; or a non-zero exit with the reason in the log, in which case the run that exists is still recorded with `--note` quoting the reason (spec 4.1).

- [ ] **Step 3: Record the three runs**

```bash
uv run python scripts/record_result.py add run_d7e6c78b53b3 --profile default --label hosted-default
uv run python scripts/record_result.py add run_378df0420b8c --profile ci-cheap --label hosted-ci-cheap --report evals/reports/ci-cheap-run_378df0420b8c.json
uv run python scripts/record_result.py add <local run id> --profile local-ollama --label local-ollama --report evals/reports/local-ollama-<run id>.json [--note "..."]
make results && make results-check
```

Expected: `docs/results/runs.json` holds three records; `docs/results/README.md`'s `runs` block lists them.

- [ ] **Step 4: Commit**

```bash
git add evals/evals.yml evals/baselines/local-ollama.json docs/results
git commit -m "feat(evals): local-ollama profile and baseline; three measured runs recorded in docs/results"
```

---

### Task 8: README front door, reference carve-out, docs index

**Files:**
- Create: `docs/reference/{run,console,api,tracing,mcp-servers,gateway,knowledge,challenger,remediation,evals,live-models,compose,configuration,commands,datasets}.md`, `docs/index.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: today's README sections by heading (spec 5.2 mapping); `record_result.py render` for the `parity` block; the GIF and MP4 from Task 6.
- Produces: `README.md` of at most 220 lines with the sections of spec 5.1 in order; `docs/index.md` per spec 5.7 (links to documents Tasks 9 to 11 will create are written now; `check_docs.py` runs clean only once they exist, so this task's check runs with those four files created as one-line stubs that Tasks 9 to 11 replace).

- [ ] **Step 1: Carve out the reference pages**

For each mapped section: new file with a one-line lead (`This page is the reference for <topic>; the README is the front door.`), the section's heading demoted to `#`, the text unchanged, a final `[Back to the README](../../README.md)`. Repoint relative links and anchors inside the moved text (links to other moved sections become `./<file>.md#...`).

- [ ] **Step 2: Write the front door**

Sections in order (spec 5.1); the two Mermaid diagrams: `flowchart LR` of the eight packages grouped in their layers with arrows in the allowed import direction, and `stateDiagram-v2` (or `flowchart TD`) of the run lifecycle `intake → scope → gate_scope → profile → split → features → build → validate → gate_model → document → challenge ⇄ remediate → gate_signoff → seal` with `paused`, `needs_attention` and the cost-cap park as side states; the `<!-- results:parity -->` block; links to `docs/index.md` and each document. Keep "Quick start" to the three commands and the gate sentence.

- [ ] **Step 3: Verify**

Run: `make results && make docs-check && make results-check && wc -l README.md`
Expected: all pass; README ≤ 220 lines; every moved section's text found verbatim in its reference page (spot-check with `diff <(sed -n 'A,Bp' <(git show HEAD:README.md)) <(sed -n '3,$p' docs/reference/gateway.md)` for two sections).

- [ ] **Step 4: Commit**

```bash
git add README.md docs/reference docs/index.md docs/design-memo.md docs/threat-model.md docs/domain-primer.md docs/trade-offs.md docs/walkthrough.md
git commit -m "docs: README front door with diagrams and measured results; reference sections moved to docs/reference; docs index"
```

---

### Task 9: Design memo and threat model

**Files:**
- Create (replace stubs): `docs/design-memo.md`, `docs/threat-model.md`

**Interfaces:**
- Consumes: umbrella sections 1, 2, 4, 10, 15, 16; SP2 to SP5 specs' as-built sections; the SP5 hand-off's SP6 recommendations; `docs/results/runs.json` (by reference through the `parity` block in `docs/trade-offs.md`, not embedded here); the proofs: `packages/api/tests/test_writes.py` (four-eyes, abort, resume), `packages/api/tests/test_identity.py`, the challenger sentinel test in `packages/orchestrator/tests`, `packages/gateway/tests` (redaction), `packages/lineage/tests/test_verify.py` or its equivalent, the import-linter contracts in `pyproject.toml`.
- Produces: the two documents per spec 5.3 and 5.4, each with its Mermaid diagrams, each naming its proofs as file paths that exist (checked by `check_docs.py` as links).

- [ ] **Step 1: Write `docs/design-memo.md`** (2,500 to 4,000 words; sections and diagrams per spec 5.3; proofs as links to the test files).
- [ ] **Step 2: Write `docs/threat-model.md`** (the trust-boundary diagram; the table of ten threats with mitigation-as-built and proof link; the SP5 exceptions section; residual risks naming Task 4's fixes as closed).
- [ ] **Step 3: Verify** — Run: `make docs-check`; read both once for the regulatory wording rule and the "not a production system" sentence.
- [ ] **Step 4: Commit** — `git add docs/design-memo.md docs/threat-model.md && git commit -m "docs: MRM-style design memo and threat model"`

---

### Task 10: Domain primer and trade-offs

**Files:**
- Create (replace stubs): `docs/domain-primer.md`, `docs/trade-offs.md`

**Interfaces:**
- Consumes: umbrella sections 2, 16, 19; `packages/core` module names (`binning.py`, `logistic.py`, `scorecard.py`, `metrics.py`, `calibration_tests.py`, `stability.py`, `sensitivity.py`, `benchmark.py`, `thresholds.py`); the thirteen validation test ids (`disc.auc.val`, `disc.auc.oot`, `disc.ks.oot`, `disc.gap.train_oot`, `cal.binomial`, `cal.hl`, `cal.eo`, `stab.psi.score`, `stab.csi.max`, `sens.signs`, `sens.monotonic`, `feat.iv_range`, `bench.gap`); the `parity` and `cost` results blocks.
- Produces: the two documents per spec 5.5 and 5.6; `docs/trade-offs.md` carries `<!-- results:parity -->` and `<!-- results:cost -->` blocks rendered by `make results`.

- [ ] **Step 1: Write `docs/domain-primer.md`** (3,000 to 4,500 words; the glossary merged from umbrella 19; each code pointer a link that resolves).
- [ ] **Step 2: Write `docs/trade-offs.md`** (the extended table with the "what it cost in practice" column citing the records' node wall times and purpose costs by name, the two blocks, two paragraphs reading the gap).
- [ ] **Step 3: Verify** — Run: `make results && make docs-check && make results-check`.
- [ ] **Step 4: Commit** — `git add docs/domain-primer.md docs/trade-offs.md && git commit -m "docs: domain primer and trade-off, parity and cost tables"`

---

### Task 11: Walkthrough and the fresh-clone measurement

**Files:**
- Create (replace stub): `docs/walkthrough.md`

**Interfaces:**
- Consumes: `docs/media/shots/*.png` (Task 6), the API routes behind each screen, `make fresh-clone-test` (Task 5).

- [ ] **Step 1: Run the fresh-clone test** — Run: `make fresh-clone-test 2>&1 | tee /tmp/fresh-clone.log` (needs port 8000 free: the controller stops the main stack's `api` first or the script refuses). Expected: `fresh-clone: awaiting_scope after <N> s`.
- [ ] **Step 2: Write `docs/walkthrough.md`** — the thirteen steps, each with its screenshot, two or three sentences on what to look at, and the API call that shows the same thing (`curl` lines); a closing "From a fresh clone" section with the measured seconds and the machine class in one neutral phrase (for example "a 2024 laptop").
- [ ] **Step 3: Verify** — Run: `make docs-check`.
- [ ] **Step 4: Commit** — `git add docs/walkthrough.md && git commit -m "docs: scripted walkthrough with screenshots and the fresh-clone timing"`

---

### Task 12: Umbrella section 23, spec as-built, final docs pass

**Files:**
- Modify: `docs/superpowers/specs/2026-11-09-modelforge-architecture-design.md` (new section 23), `docs/superpowers/specs/2026-11-11-sp6-docs-presentation-design.md` (section 10), `docs/index.md`

- [ ] **Step 1: Umbrella section 23 "As-built amendments from SP6 (2026-11-11)"**: bullets for 17 row 6 as built (the video is a scripted recording plus walkthrough; the reference moved to `docs/reference/`; the results file and its blocks; the `docs` CI job; the manual `demo.yml`; the local-ollama measurement), 12 (abort lock order; `report_missing`), 14 (the `local-ollama` eval profile), 15 (the SP5 exceptions now documented).
- [ ] **Step 2: Spec section 10**: every deviation made during Tasks 1 to 11, one bullet each, cited by task.
- [ ] **Step 3: `docs/index.md` final pass**: times per document, the "how it was built" list including this plan and spec.
- [ ] **Step 4: Verify everything** — Run: `make lint && make docs-check && make results-check && uv run pytest scripts/tests -q && npm test --prefix console`.
- [ ] **Step 5: Commit** — `git add docs && git commit -m "docs: umbrella section 23 and SP6 as-built amendments"`

---

## Controller notes (not tasks)

- Before Task 7: stop the main stack's `worker` container (`COMPOSE_PROJECT_NAME=laughing-neumann-d38356 docker compose stop worker`) so the harness's in-process execution is the only worker on the development database; restart it afterwards. Before Task 11's fresh-clone run: stop that stack's `api` (port 8000) or set the script's expectations accordingly.
- Reviewer models: Sonnet for Tasks 1, 2, 5, 8, 10, 11, 12; Opus for Tasks 3, 4, 6, 7, 9. Implementers: Opus throughout (the user's standing rule).
- After the final review and the finishing menu, the controller: pushes (only on the user's answer), runs `demo.yml` once from GitHub and records its URL in the hand-off note, sets the repository topics and description, and writes `docs/superpowers/notes/2026-11-11-sp6-handoff.md`.
