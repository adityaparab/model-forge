# SP6: documentation, measurements and presentation (design)

Date: 2026-11-11. Umbrella: `docs/superpowers/specs/2026-11-09-modelforge-architecture-design.md`, section 17 row 6 ("README; architecture diagram; MRM-style design memo; threat model doc; trade-off and cost tables; domain primer; demo video; fresh-clone test of `make demo`"; contract handed on: "the public repo"). Predecessor: `docs/superpowers/notes/2026-11-10-sp5-handoff.md`.

## 1. Purpose

SP6 turns the repository into the artefact a reviewer opens: a hiring panel of engineers, and possibly model-risk people, reading `https://github.com/adityaparab/model-forge` with an afternoon. It adds no Python package and changes no public interface. It delivers the umbrella's success criteria 1 (one command, a demo within minutes), 6 (the measured quality and cost gap between the hosted model and a local one, published) and 7 (a senior reviewer reads the stack in an afternoon), and it closes two small defects SP5 parked.

Decisions taken in brainstorming (2026-11-11, with the user):

- The demo video is a scripted Playwright recording of the real console on the `fake` profile, plus a written walkthrough built from the same script's screenshots.
- One local run is measured, `local-ollama` on `gpt-oss:20b`; LM Studio is not measured.
- The new documents are Markdown under `docs/`, no PDF, no site.
- Diagrams are Mermaid blocks in Markdown.
- The README becomes a front door of about 200 lines; its reference sections move to `docs/reference/` with their text unchanged.
- Approach "measure first, then write": the tooling and the measured runs come before the documents, so every published number is read from a committed results file.
- Two SP5-parked fixes are in scope (section 7); the worker lease and the SSE cursor are not.

## 2. Repository layout after SP6

```
README.md                         front door (180 to 220 lines)
docs/
  index.md                        reading order for an afternoon, one line per document
  design-memo.md                  MRM-style design memo
  threat-model.md                 trust boundaries, threats, proofs, SP5 exceptions, residual risks
  domain-primer.md                credit-risk and model-risk primer for an engineer (30 minutes)
  trade-offs.md                   deterministic vs LLM; hosted vs local parity; cost
  walkthrough.md                  the scripted demo as numbered steps with screenshots
  reference/                      the README's deep sections, moved not rewritten (one file per section)
    run.md console.md api.md tracing.md mcp-servers.md gateway.md knowledge.md
    challenger.md remediation.md evals.md live-models.md compose.md configuration.md
    commands.md datasets.md
  results/
    runs.json                     one record per measured run (section 4.2)
    README.md                     how a record is produced and added
  media/
    demo.mp4 demo.gif             the recording (GIF under 8 MiB, MP4 under 25 MiB)
    shots/NN-<step>.png           walkthrough screenshots (1440 x 900)
scripts/
  package.json                    pinned Playwright and Mermaid CLI for the docs tooling (not the console's)
  demo_record.mjs                 drives the console on the fake profile; records video; shoots steps
  record_result.py                reads one sealed run into docs/results/runs.json; renders tables
  check_docs.py                   offline checks on README.md and docs/** (section 6)
  fresh_clone_test.sh             clone origin/main into a temp dir, make demo, wait, tear down
  tests/                          pytest for record_result.py and check_docs.py on fixtures
.github/workflows/demo.yml        manual: the fresh-clone test on an Ubuntu runner
Makefile                          demo-record, results, results-check, docs-check, fresh-clone-test
```

Rules: the `docs/superpowers/` tree (specs, plans, notes) stays where it is and is linked from `docs/index.md` as "how it was built". Nothing under `docs/media/` is generated in CI; the recording is committed. `uv.lock` and the console's `package.json` do not change; the docs tooling has its own `scripts/package.json`.

## 3. Tooling

### 3.1 `scripts/demo_record.mjs`

Node ESM, run with the Playwright version pinned in `scripts/package.json` (an exact version at least two weeks old whose Chromium build is the one cached on the author's machine, so `npx playwright install chromium` is a no-op there and a download elsewhere). Arguments: `--base` (default `http://localhost:8000`), `--out` (default `docs/media`), `--brief` (default `examples/briefs/polish-pd.yaml`), `--no-video` (screenshots only).

Behaviour: a 1440 x 900 context with `recordVideo`; identity is typed into the top-bar field; every step waits on page text or an element state, never on a timer, so the fake profile makes it deterministic. Steps, each saving `shots/NN-<step>.png`:

1. `01-runs` the run list as `alice`.
2. `02-new-run` the New run page with the brief loaded, submitted.
3. `03-scope-gate` the run page at the scope gate, plan JSON visible.
4. `04-scope-approved` after `approve` as `alice`: status `running`, the live feed moving.
5. `05-model-gate` the model gate with the validation outcomes.
6. `06-four-eyes` `accept` attempted as `alice`, the four-eyes refusal shown under the form.
7. `07-model-accepted` `accept` as `bob`.
8. `08-document` the Document tab with one citation popover open.
9. `09-findings` the Findings tab, round 1 open.
10. `10-lineage` the Lineage tab after `Verify` reports ok.
11. `11-costs` the Costs page.
12. `12-signoff` the sign-off gate, `approve` as `bob`.
13. `13-sealed` the sealed run with the Replays panel showing the automatic replay (waits for "Reproduced").

On exit the script runs ffmpeg on the WebM: `demo.mp4` (H.264, 1440 wide, yuv420p, faststart) and `demo.gif` (720 wide, 10 fps, two-pass palette). It fails if the GIF exceeds 8 MiB or the MP4 25 MiB. `make demo-record` starts a stack under `COMPOSE_PROJECT_NAME=modelforge-record MODELFORGE_POSTGRES_PORT=5433 MODELFORGE_AUTO_REPLAY=1`, waits for health, runs the script, and tears the stack down with `-v`.

### 3.2 `scripts/record_result.py`

Python run through `uv run` (it imports `modelforge_lineage` and `modelforge_evals`; `scripts/` is not a package under the import-linter contracts). Two modes.

`record_result.py add <run_id> --profile <name> --label <text> [--report <path>]`: with no `--report`, it runs `modelforge-evals run --profile <name> --run-id <run_id> --layers pipeline,retrieval` so the harness evaluates the existing sealed run without any LLM call and writes `evals/reports/<profile>-<run_id>.json`; then it reads that report, the run record, its approvals and its replays through lineage, and writes one record into `docs/results/runs.json` keyed by `run_id` (replace on repeat). The record (section 4.2) is JSON with sorted keys and a trailing newline so diffs are stable.

`record_result.py table parity|cost|runs`: prints the Markdown table for that block. `record_result.py render <file>...`: rewrites the text between `<!-- results:<name> -->` and `<!-- /results:<name> -->` markers in the given files; `render --check` exits 1 when a file would change. `make results` renders `README.md` and `docs/trade-offs.md`; `make results-check` checks them, and CI runs the check.

### 3.3 Fresh-clone test

`scripts/fresh_clone_test.sh [remote-url]`: `git clone --depth 1` of the URL (default `origin`'s) into `mktemp -d`; in the clone, `COMPOSE_PROJECT_NAME=modelforge-fresh MODELFORGE_POSTGRES_PORT=5434 make demo`; poll `GET /api/v1/runs/<id>` until `awaiting_scope` (timeout 20 minutes); print the elapsed seconds from clone to gate; `docker compose down -v` and remove the clone in a `trap`. The API port stays 8000, so the script refuses to start when something already answers on it. `make fresh-clone-test` runs it.

`.github/workflows/demo.yml`: `workflow_dispatch` only; `ubuntu-latest`; `timeout-minutes: 30`; steps: checkout, run `scripts/fresh_clone_test.sh https://github.com/${{ github.repository }}.git`, and on failure upload `docker compose logs api worker` as an artifact. It is manual because it builds the image and downloads the dataset; `ci.yml` remains the push gate.

## 4. Measurements

### 4.1 The three runs

| Record | Profile | Models | How it is produced |
|---|---|---|---|
| `hosted-default` | `default` | `claude-opus-5-5` for every purpose | run `run_d7e6c78b53b3`, submitted on 2026-11-11 through `POST /runs?profile=default&cost_cap_usd=20`, gates decided in the console (`alice` approve, `bob` accept, `bob` sign-off); evaluated with `record_result.py add`. If that run does not seal, the record is the run that does, started the same way, and the hand-off note says what happened to the first |
| `hosted-ci-cheap` | `ci-cheap` | `claude-sonnet-5-5` | the harness run of 2026-11-11 that wrote `evals/baselines/ci-cheap.json` (`run_378df0420b8c`, 3.19 USD); recorded from its existing report |
| `local-ollama` | `local-ollama` | `gpt-oss:20b` through Ollama on the author's machine | `modelforge-evals run --profile local-ollama --layers pipeline,retrieval --write-baseline evals/baselines/local-ollama.json`, with the worker reaching Ollama at the host; wall time per node is part of the record |

If the local run cannot finish within two hours, exhausts a retry budget or parks at a gate the harness cannot decide, the record carries `"status"` as it ended and `"note"` saying so, and the parity table shows the row with that status rather than omitting it. No other live spend is in scope; the ci-cheap and default runs above have already been paid for.

### 4.2 The record

```json
{
  "run_id": "run_...", "label": "hosted-default", "profile": "default",
  "models": {"scope": "claude-opus-5-5", "document": "...", "challenge": "...", "remediate": "..."},
  "recorded_at": "2026-11-11T...Z", "orchestrator_version": "0.1.0",
  "status": "completed", "signoff": "approve_with_conditions",
  "grounding_pass_rate": 0.0, "completeness_complete": true, "n_flagged_sections": 0, "n_ungrounded_numbers": 0,
  "n_sections": 11, "n_sections_regenerated": 0,
  "findings": {"total": 0, "precheck": 0, "review": 0, "high": 0, "medium": 0, "low": 0, "open_at_signoff": 0},
  "rounds": {"challenge": 0, "remediation": 0},
  "scope_exact_match": true, "retrieval_recall_at_5": 1.0,
  "n_llm_calls": 0, "n_schema_retries": 0, "input_tokens": 0, "output_tokens": 0,
  "cost_usd": 0.0, "cost_by_purpose": {"scope": 0.0, "document": 0.0, "challenge": 0.0, "remediate": 0.0},
  "wall_time_s": 0.0, "wall_time_by_node_s": {"scope": 0.0, "document": 0.0},
  "replay_ok": true, "note": null
}
```

Every field comes from the eval report or lineage; `record_result.py` never takes a metric on the command line. `cost_usd` for a local run is 0 by the profile's own accounting, and the parity table says "0 (local)".

### 4.3 The tables

- `parity`: one row per record: profile and model, status and sign-off, grounding pass rate, completeness, ungrounded numbers, findings (pre-check / review; high / medium / low), rounds (challenge / remediation), sections regenerated, schema retries, LLM calls, cost, wall time.
- `cost`: one row per record: input tokens, output tokens, cost by purpose, total; and the per-run cap.
- `runs`: run id, label, recorded date, orchestrator version, replay ok (the provenance table in `docs/results/README.md`).

The README embeds `parity`; `docs/trade-offs.md` embeds `parity` and `cost`; `docs/results/README.md` embeds `runs`.

## 5. The documents

Rules for every document: employer-neutral (no company names, no machine paths); "a reference implementation, not a production system" stated once; regulatory wording follows umbrella section 2 exactly, in particular SR 26-2 superseded SR 11-7 and SR 21-8 in April 2026 and places generative and agentic AI outside its scope, and no document claims that SR 26-2 extends SR 11-7 to LLMs or names LLM-as-judge or adversarial probing as validation techniques; every number comes from `docs/results/runs.json` through a results block or names the test or contract that proves the claim; headings are sentence case; Mermaid for every diagram.

### 5.1 README front door

In order: title and one paragraph (what ModelForge is, the governance gap it demonstrates, what a reviewer will see); badges (`ci`, license); the demo GIF (`docs/media/demo.gif`) with a link to `docs/walkthrough.md` and the MP4; "Try it" (prerequisites in one line, the three commands from today's Quick start, what appears within minutes, where the gates are); "How it works" (Mermaid: the eight packages in their layers with the import-linter direction; Mermaid: the run lifecycle with the three gates and the review loop); "What it demonstrates" (six bullets mapping to the success criteria, each pointing at a document or a test); "Measured" (the `parity` results block and two sentences on the gap); "Read next" (`docs/index.md` order); "Not a production system" (identity is a header not authentication; dataset proxies; local model quality; no monitoring); "Status" and license. Target 180 to 220 lines.

### 5.2 `docs/reference/`

Fifteen of today's README sections become one file each, with the same heading and text plus a one-line lead sentence and a "Back to the README" link: "Run a governed model build" (`run.md`), "Console" (`console.md`), "API" (`api.md`), "Tracing" (`tracing.md`), "MCP servers" (`mcp-servers.md`), "LLM gateway" (`gateway.md`), "Knowledge layer" (`knowledge.md`), "Challenger review" (`challenger.md`), "Remediation loop" (`remediation.md`), "Evals" (`evals.md`), "Running against a real model" (`live-models.md`), "Compose" (`compose.md`), "Configuration" (`configuration.md`), "Commands" (`commands.md`) and "Datasets" (`datasets.md`). "Quick start" and "Status" stay in the README in shortened form. Relative links inside the moved text are repointed. The move is reviewable as a move: a reviewer diffing the old README section against the new file sees only the lead line, the link and path fixes.

### 5.3 `docs/design-memo.md`

Audience: a model-risk reviewer. Sections: purpose and intended use; what the model is (the WoE logistic scorecard with its LightGBM challenger) and what the agents are (tooling around it), and why the distinction decides what gets validated; conceptual soundness of the pipeline step by step with the deterministic-or-LLM choice and the reason (from umbrella section 16, extended); controls, each with the test or contract that proves it: human gates and four-eyes (`packages/api/tests/test_writes.py`), cost cap and bounded loops, challenger isolation (the sentinel test), redaction, lineage and the sealed manifest (`verify`), replay, identity recording; known limitations; validation evidence (the `parity` block by reference, the eval harness layers, the planted-defect recall); what a bank would have to change before use (authentication, a worker lease, monitoring, a real inventory process, model and prompt change control); regulatory framing in the umbrella's words. Two Mermaid diagrams: the trust boundary between the developer agent and the challenger, and the lineage chain from brief to manifest.

### 5.4 `docs/threat-model.md`

A Mermaid trust-boundary diagram (browser, API, worker with in-process MCP servers, gateway to providers, Postgres and the artifact store, Jaeger and Phoenix); the umbrella's ten threats, each with the mitigation as built and the proof named as a file or contract; "Exceptions introduced by SP5" as its own section: the identity header is identity, not authentication; `MODELFORGE_TRACE_CONTENT=1` sends prompts and completions to the configured collectors; model-derived HTML is served from the console's origin behind `Content-Security-Policy: sandbox` and `X-Content-Type-Options: nosniff`; "Residual risks": the worker lease (a dead worker leaves a run `running`), the global SSE stream's window, and the two fixes of section 7 recorded as closed.

### 5.5 `docs/domain-primer.md`

For an engineer with no credit-risk background, readable in 30 minutes: what a PD model is and who uses it; the IRB vocabulary (PD, LGD, EAD, vintage, out-of-time) and the proxies this project uses; how a scorecard is built (binning, WoE, IV, logistic fit, points) with pointers to `packages/core`; how it is validated, mapping the thirteen tests of the validation server to discrimination, calibration, stability, sensitivity and benchmark; what model risk management does (inventory, validation, effective challenge, approval, monitoring) and where each appears in the console; the regulatory landscape in the umbrella's framing; a glossary merged from umbrella section 19.

### 5.6 `docs/trade-offs.md` and `docs/walkthrough.md`

`trade-offs.md`: the umbrella's deterministic-versus-LLM table with a fourth column "what it cost in practice" (from the records: for example the LLM steps' share of wall time and cost); the `parity` and `cost` results blocks; a reading of the gap in at most two paragraphs. `walkthrough.md`: the thirteen steps of section 3.1, one screenshot each, with what to look at and where the same thing is in the API, ending with the fresh-clone elapsed time measured on the author's machine.

### 5.7 `docs/index.md`

The reading order: README (10 minutes), walkthrough (10), design memo (30), domain primer (30), threat model (15), trade-offs (10), then the reference pages and the `docs/superpowers/` specs, plans and hand-off notes as "how it was built", with a sentence on the sub-project structure.

## 6. Verification

`ci.yml` gains a `docs` job, offline, after `lint-test`: `npm ci --prefix scripts`; `make results-check`; `make docs-check`, which runs `scripts/check_docs.py` and the Mermaid check. `check_docs.py` verifies: every relative link and image in `README.md` and `docs/**/*.md` resolves to a file (anchors to a heading in that file); `docs/media/demo.gif` is under 8 MiB and `demo.mp4` under 25 MiB; no tracked file matches the neutrality patterns: the generic ones are in the script (a `/Users/<name>/` or `/home/<name>/` path, a `.claude/worktrees/` path, `postgresql://` with credentials outside `docker-compose.yml`, `.env.example` and the README's documented default), and private ones (the employer's name and the author's machine names) are read from an optional git-ignored `.neutrality-patterns` file or the `MODELFORGE_NEUTRALITY_PATTERNS` variable, so the forbidden words never appear in a tracked file; CI runs the generic set and the author runs the full set before pushing; the README is at most 220 lines; every Markdown file with a Mermaid block passes `mmdc` (the pinned `@mermaid-js/mermaid-cli`, each block rendered to a temporary SVG). `scripts/tests/` holds pytest tests for `record_result.py` (a fixture report and run give the documented record; `table` output is stable; `render --check` detects a stale block) and `check_docs.py` (a broken link, an oversized GIF, a neutrality hit and a bad Mermaid block each fail with a named reason); `pyproject.toml`'s pytest `testpaths` gains `scripts/tests`.

`demo.yml` is run once from GitHub before SP6 closes and its run URL goes in the hand-off note. `make fresh-clone-test` is run once on the author's machine and its elapsed time goes in `docs/walkthrough.md`.

## 7. Hardening (SP5 parked items)

- API and CLI `abort`: call `runs.transition` before `queue.release` so the lock order (run row, then queue row) matches `resume`'s; test: a resume holding the run row lock while an abort runs no longer deadlocks (the abort waits, then is refused or succeeds by status).
- `queries.replay_items` and `GET /runs/{id}/replays`: a replay whose report artifact is missing from the store is returned with `report: null` and `report_missing: true` (new `ReplayItem` field, default `false`) instead of a 404, and `GET /runs/{id}` no longer 404s for it; the Replays panel shows "report missing" for such a row; the OpenAPI document, types and fixtures are regenerated. Spec SP5 section 13's bullet on the 404 is amended.

## 8. Process and constraints

Subagent-driven development as in SP2 to SP5: a plan with one task per deliverable; Opus implementers; Sonnet task reviewers, Opus for the scripts and the hardening; Fable for the final whole-branch review; the SDD ledger in the worktree. Commit messages carry no attribution lines of any kind (the user's instruction of 2026-11-11). Nothing is merged or pushed without the user's answer from the finishing menu; the public repo is the one that gets pushed. No live provider spend beyond the two runs already made; the local run is free. Tests and CI stay offline; the recording and the fresh-clone test are not CI steps except the manual `demo.yml`.

## 9. Done criteria

1. `make fresh-clone-test` reaches `awaiting_scope` on the author's machine and the elapsed time is in `docs/walkthrough.md`; the `demo.yml` run on GitHub is green.
2. The README is at most 220 lines and renders its GIF, two diagrams and the parity block on GitHub; the `ci` and `docs` jobs are green on the pushed `main`.
3. `docs/results/runs.json` holds the three records of 4.1 (the local one possibly with a `note`), and every number in the README and `docs/trade-offs.md` comes from a results block.
4. `docs/design-memo.md`, `docs/threat-model.md`, `docs/domain-primer.md`, `docs/trade-offs.md`, `docs/walkthrough.md`, `docs/index.md` and the fifteen reference pages of section 2 exist and pass `check_docs.py`.
5. The two hardening fixes are in with their tests; the SP5 hand-off's parked lines and the SP5 spec's section 13 are amended.
6. Umbrella section 23 records SP6 as built; `docs/superpowers/notes/2026-11-11-sp6-handoff.md` closes the sub-project in the shape of the SP5 note.

## 10. As-built amendments

Recorded during execution, one bullet per task, with the sections of this spec (and of the umbrella) each amends; the umbrella's section 23 carries the ones that change it.

- Task 2 (spec 6): `check_docs.py` gates the README-length rule behind `--strict-readme` (on in `make docs-check` since Task 8); the Mermaid check runs `mmdc` with `PUPPETEER_EXECUTABLE_PATH` set to the Playwright Chromium (resolved from `scripts/`), failing loudly when no browser exists; the CI `docs` job installs the Playwright Chromium once; a `puppeteer.json` with `--no-sandbox` for Linux runners. Neutrality: the generic patterns live in the script, private ones in the git-ignored `.neutrality-patterns` file or `MODELFORGE_NEUTRALITY_PATTERNS`; the credential pattern's allowlist also covers the lineage testing helper, package tests and `docs/superpowers/*`; the SP6 plan and spec are exempt from the path and worktree patterns because they quote them; test fixtures are exempt from every pattern.
- Task 3 (spec 4.2 and 4.3): the record carries `cost_cap_usd` (the cost table's Cap column) and the findings block counts `critical` as well as high, medium and low (Task 7); `RECORD_KEYS` is exported; `build_record` takes an optional `recorded_at`; `replay_ok` is null (rendered `n/a`) without a completed plain replay; `render --check` normalises CRLF and the trailing newline; `table` with no records prints one `no records yet` row.
- Task 4 (spec 7; umbrella 12): `abort` (API and CLI) locks the run through `runs.transition` before `queue.release`; `ReplayItem.report_missing` flags a replay whose report artifact is missing instead of a 404; the SP5 spec section 13 and hand-off amended.
- Task 5 (spec 3.3): `--dry-run` performs no port check and `--port-check-only` performs only the check (exit 3 when busy); exit 5 when `make demo` fails or prints no run line; the default remote comes from `git remote get-url origin`; 200 log lines saved and 50 printed on timeout; `make demo` skips its `open` step under `MODELFORGE_DEMO_NO_OPEN`.
- Task 6 (spec 3.1; umbrella 17 row 6): the GIF is 8 fps (10 fps exceeded 8 MB); the capture order is 01-09, 12, 10, 11, 13 because `Verify chain` is enabled only after the seal (file names unchanged); step 13 reloads between 5-second waits for the badge; the recording stack's default profile is Compose's `fake`.
- Task 7 (spec 4.1; umbrella 14 and 17 row 6): the local-ollama measurement applied the two-hour clause: attempt 1 (`run_41b058364191`) ran scope, document, challenge and remediate on `gpt-oss:20b` and ended `needs_attention` on a schema error after three retries at 29 minutes; attempt 2 was terminated after 7 h 29 min in the document node; the record is attempt 1, built with `add --from-lineage` (eval-only metrics null, rendered `-`; rounds count succeeded steps only; `open_at_signoff` null for an unsealed run); no `local-ollama` baseline was written; the `local-ollama` profile entry in `evals/evals.yml` stays for repeatability; `add` moves a pre-existing target report to `<name>.bak` before invoking the harness (deleted it until the final wave) and refuses when none is written; the harness's exit 1 on a `--run-id` evaluation (profile rules over absent defect metrics) is tolerated only when the report was written.
- Task 8 (spec 5.1 and 5.2): the README is 157 lines (under the 180-line guide; the 220-line rule binds); the fifteen reference pages differ from the old sections only by the lead line, the heading level and the back link (the old README had no relative links); the prerequisites name Docker with Compose v2, `make`, `curl` and `python3`.
- Task 9 (spec 5.3 and 5.4; umbrella 15): the worker starts the MCP servers as stdio subprocesses with an allowlisted environment (not in-process; the documents follow the code); the threat model adds two residual risks (floating base-image tags without digests; single-operator assumptions: no TLS, no rate limiting, Phoenix authentication off) and records that no dedicated prompt-injection eval case exists; `/config` strips credentials from URLs rather than reporting key presence.
- Task 10 (spec 5.6): the trade-off table carries a Remediation row the umbrella's table folds into effective challenge; the primer states configured thresholds and scorecard scaling with pointers to `schemas.py` (settings, not measurements).
- Task 11 (spec 3.3 and 9): the fresh-clone elapsed seconds are typed into `docs/walkthrough.md` (the only measured number outside a results block, as the spec requires).
- Task 12 (spec 5.7 and 9): the reading order's minutes follow the final lengths (design memo and domain primer 25 each, threat model 10; the six documents together stay under two hours); `docs/index.md` lists each sub-project's design, plan and hand-off note in one table, the first sub-project's design being section 17.1 of the umbrella; the README's status names the six sub-projects complete; the SP6 hand-off note is committed as a stub the controller overwrites.
- Final wave (spec 5.2): the reference pages were moved verbatim; five sentences then refreshed for SP6's own changes in a separate commit (the `ci-cheap` run and baseline now recorded, the repository public with CI, the parity table's new home, the replays' `report_missing` field), together with the same `report_missing` correction in the walkthrough.
- Process: every commit without attribution lines; the demo workflow (`demo.yml`) is run from GitHub only after the push, by the controller, with its URL in the hand-off note.
