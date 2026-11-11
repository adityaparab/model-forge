This page is the reference for the eval harness; the README is the front door.

# Evals

Evaluation runs in five layers:

1. **Unit** tests in `packages/*/tests` (pytest).
2. **Tool contracts**: a JSON-schema snapshot and hashed golden outputs per MCP tool (`packages/mcp/contracts`), checked by pytest.
3. **End to end under `fake`**: the whole graph through the three gates, the challenger and remediation loop included, in pytest (the Polish run is marked slow).
4. **The eval harness** (`packages/evals`, the `modelforge-evals` command): drives a brief through the real graph with scripted gate decisions and measures retrieval, the pipeline, the challenger and cost against a baseline.
5. **Provider parity**: the results table in "Running against a real model", one row per profile.

The LLM judge of document quality is deferred: the `judge` purpose is routed in every profile but nothing calls it yet.

**Configuration.** `evals/evals.yml` names the briefs (`synthetic`, `polish`), the scripted approvals (alice approves the scope, bob accepts the model and approves the sign-off; when the sign-off rule refuses `approve`, the harness records `approve_with_conditions` with the refusal as its condition) and one entry per profile: the brief it runs, its layers (`retrieval`, `pipeline`, `challenger`, `cost`) and how its report is compared. `fake` runs the synthetic brief and compares every metric exactly except timing; `default` and `ci-cheap` run the Polish brief and compare only the metrics they list, each with a tolerance (a negative one is the largest allowed fall from the baseline, a positive one the largest allowed rise), plus absolute budgets on cost (10 and 5 USD) and wall time (3600 and 2700 s); the `default` budgets are the measured `default` run's figures plus a margin.

**Goldens.** `evals/goldens/retrieval.yaml` holds 26 questions, each naming a corpus document and a phrase: a question counts as found when one of the top five hits comes from that document and contains the phrase. The knowledge unit test reads the same file. `evals/goldens/defects.yaml` is the planted-defect suite, six classes, each a mutation of the completed run's rendered document and the finding it should provoke:

| Class | Mutation | Expected finding |
| --- | --- | --- |
| `removed_section` | drop the monitoring section | key `missing_section:monitoring` |
| `swapped_number` | change one digit of a grounded number in the performance section | key `reverify:performance` |
| `claimed_absent_test` | append a sentence claiming an independent bootstrap stability test was performed and passed | category `performance`, evidence naming `performance` |
| `miscited_regulation` | replace the first passage marker in the regulatory context section with a passage of the IFRS 9 summary | key `miscited_regulation:regulatory_context:*` |
| `undiscussed_breach` | remove the performance sentences that cite a failing or warning test | key `undiscussed_test:*` |
| `missing_limitation` | remove the limitations sentences that mention a proxy | category `data`, evidence naming `limitations` |

Each case runs the real `challenge` node on a probe run of its own (requester `evals`, the base run's profile and plan, the mutated document, its recomputed completeness and copies of the artifacts the document cites, remediation bound 0), so the probe's calls, findings and events never touch the base run. A probe is marked completed without a manifest and is never replayed. A finding matches a case by its pre-check key (a glob) or, failing that, by category with the section among its evidence. Recall per class is 1 or 0, and not applicable when the mutation changes nothing; `defect_recall` is the mean over the applicable classes. On applicable probes, a finding that matches neither its case nor a finding the base run already has (by key, else by category and title) is a false positive, and `defect_precision` is matched findings over matched plus false positives.

**Metrics.** `retrieval_recall_at_5`; from the pipeline run, `pipeline_status`, `signoff_decision`, `grounding_pass_rate` (1 minus flagged sections over sections), `n_flagged_sections`, `n_ungrounded_numbers`, `completeness_complete`, `scope_exact_match`, `challenge_rounds`, `remediation_rounds`, `n_findings` and findings by source, severity and status, `n_sections_regenerated`; from the probes, `defect_recall`, the per-class recalls, `defect_precision` and `probe_cost_usd`; and from the run's `llm_calls` and `steps` rows, `cost_usd` in total and per purpose, `n_llm_calls`, `n_schema_retries`, input and output tokens, latency percentiles and wall time in total and per node.

**Reports and baselines.** Each run's report (no timestamps; it also names the models, the prompt hashes and the orchestrator version) is stored as an `eval_report` artifact on the pipeline run and written to `evals/reports/<profile>-<run_id>.json` (git-ignored); a Markdown summary goes to stdout and, when `GITHUB_STEP_SUMMARY` is set, to the job summary. Baselines are committed per profile in `evals/baselines/<profile>.json`. In the committed `default` baseline `defect_recall_by_class.swapped_number` is `null`: on the measured run the `swap_digit` mutation took the first marker sentence of the performance section, which carried no number, so that probe was inapplicable; the mutation now takes the first marker sentence that contains a number, and a later live run fills the class in.

```bash
make evals                       # fake profile, compared with evals/baselines/fake.json
make evals-live PROFILE=default  # a live profile against its committed baseline (needs the key)
make evals-baseline              # rewrite evals/baselines/fake.json from a fresh fake run
uv run modelforge-evals run --profile <name> [--brief <key>] [--layers retrieval,pipeline,...] \
    [--baseline PATH] [--write-baseline PATH] [--report-dir DIR] [--run-id ID] [--json]
uv run modelforge-evals compare <report.json> <baseline.json> --profile <name>
```

The harness needs a migrated database, like `modelforge run`. `--write-baseline` with `--baseline` writes the new baseline only when nothing regressed. `--run-id <run_id>` evaluates an existing completed, sealed run under the same profile instead of driving a new one (the report takes the decisions recorded on that run), for a live run that parked at its cost cap and was finished by hand with `modelforge resume --cost-cap` and the remaining approvals. Exit codes: 0 clean; 1 a regression against the baseline or a budget exceeded; 2 a configuration error (bad `evals.yml`, unknown profile, brief or layer, `challenger` without `pipeline`, an unreadable baseline), a pipeline run that parked, or any other failure while the layers ran. The baseline is read before any layer runs, so a missing one fails before anything is spent.

**CI.** `ci.yml` runs `make migrate` and `make evals` after `make test`, offline under `fake`. `.github/workflows/evals.yml` runs the `ci-cheap` profile against `evals/baselines/ci-cheap.json` on push to `main`, nightly at 03:00 UTC and on a pull request labelled `run-evals`, with a Postgres service, `ANTHROPIC_API_KEY` from the repository secrets and a cost cap of 8 USD (`MODELFORGE_COST_CAP_USD`), which applies to the pipeline run and to each probe run separately, since every probe is a run of its own carrying the base run's cap; the live profiles also budget `probe_cost_usd`, the probes' total (4 USD under `default`, 1 USD under `ci-cheap`), and the workflow uploads the reports. It needs that secret and a committed `ci-cheap` baseline; without the baseline it exits 2 before running anything. The project has no remote yet, so the workflow is committed but has not run.

**Results under `fake`** (the synthetic brief, from `evals/baselines/fake.json`):

| Metric | Value |
| --- | --- |
| Pipeline | completed, signed off with `approve` |
| Findings | 5 (3 pre-check, 2 review: 4 closed, 1 disputed; 4 medium, 1 low) |
| Rounds (challenge / remediation) | 2 / 1 |
| Sections regenerated | 2 |
| LLM calls | 35 (0 schema retries), cost 0 |
| Grounding pass rate | 1.0 (0 of 11 sections flagged, 0 ungrounded numbers) |
| Retrieval recall at 5 | 1.0 |
| Defect recall | 0.8 (`removed_section`, `swapped_number`, `claimed_absent_test` and `miscited_regulation` 1.0; `missing_limitation` 0.0; `undiscussed_breach` not applicable) |
| Defect precision | 1.0 |

The three pre-check findings are the passing out-of-time and sensitivity results the scripted document leaves unreported (`disc.ks.oot`, `sens.signs`, `sens.monotonic`), closed when the performance section was regenerated. `undiscussed_breach` is not applicable on the synthetic document: removing the performance sentences that cite a failing or warning test changes nothing there. `missing_limitation` is missed because the scripted proxy finding needs a plan whose target names a proxy, and the synthetic brief's does not. Like the rest of `fake`, these numbers show that the harness measures what it should, not how well a model reviews.

[Back to the README](../../README.md)
