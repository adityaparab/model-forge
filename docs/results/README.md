# Results

Every number the documents publish about a run comes from `runs.json` in this directory. A record is written by a script from a sealed run's eval report and its lineage; no metric is typed by hand, and no document states a measured number outside a results block.

## How a record is produced

```bash
uv run python scripts/record_result.py add <run_id> --profile <profile> --label <label>
```

`add` runs the eval harness against the existing sealed run (`modelforge-evals run --profile <profile> --run-id <run_id> --layers pipeline,retrieval`, which makes no LLM call) and writes `evals/reports/<profile>-<run_id>.json`. The harness exits 1 when a rule of the profile fails, which it does for a profile with rules over the defect and probe metrics, since these two layers do not measure them; the report is written before that check, so `add` records it. `add` deletes any earlier report at that path before it runs the harness and stops on any other exit or when no fresh report was written. Each such `add` also stores another `eval_report` artifact on the run. It then reads that report, the run record, its approvals and its replays from lineage and writes one record into `runs.json`, keyed by `run_id`: recording the same run again replaces its record. `--report <path>` uses an existing report instead of running the harness, and `--note <text>` attaches a note. `--from-lineage` records a run that never sealed, which the harness refuses to evaluate: the record is built from the run's lineage rows alone (its LLM calls, steps and findings), and the metrics only an eval report has (grounding, completeness, sections, scope match, retrieval recall) are `null`, shown as `-` in the tables. The file is JSON with sorted keys and a trailing newline so diffs stay small.

`make results` re-renders every results block in `README.md`, `docs/trade-offs.md` and this file; `make results-check` fails when a block is stale, and CI runs it.

## What each field means

| Field | Meaning |
| --- | --- |
| `run_id` | The sealed run the record describes. |
| `label` | A short name for the run in the tables (for example `hosted-default`). |
| `profile` | The configuration profile the run used. |
| `models` | The model each purpose (`scope`, `document`, `challenge`, `remediate`) was routed to. |
| `recorded_at` | When the record was written (UTC). |
| `orchestrator_version` | The orchestrator version that produced the run. |
| `status` | The run's final status in lineage. |
| `signoff` | The decision of the last sign-off approval. |
| `grounding_pass_rate` | The share of document sections whose numbers all trace to evidence. |
| `completeness_complete` | Whether every required section is present. |
| `n_flagged_sections` | Sections flagged by the completeness check. |
| `n_ungrounded_numbers` | Numbers in the document that trace to no evidence. |
| `n_sections`, `n_sections_regenerated` | Sections written, and sections rewritten by remediation. |
| `findings` | Findings in total, by source (`precheck`, `review`), by severity (`critical`, `high`, `medium`, `low`), and still open at sign-off (`null` for a run that never reached sign-off). |
| `rounds` | Challenge and remediation rounds completed: from the sealed manifest's findings and responses for a harness report, from the challenge and remediate steps that succeeded for a `--from-lineage` record. |
| `scope_exact_match` | Whether the proposed modeling plan matched the expected plan exactly. |
| `retrieval_recall_at_5` | Recall at 5 of the knowledge layer on the eval queries. |
| `n_llm_calls`, `n_schema_retries` | LLM calls made, and calls retried for a schema failure. |
| `input_tokens`, `output_tokens` | Tokens across all LLM calls. |
| `cost_usd`, `cost_by_purpose` | Cost by the profile's own prices, in total and per purpose; 0 for a profile without prices, shown as `0 (local)`. |
| `cost_cap_usd` | The run's cost cap. |
| `wall_time_s`, `wall_time_by_node_s` | Wall time of the run, in total and per pipeline node. |
| `replay_ok` | Whether the latest completed replay without LLM reruns reproduced the run; `null` when there is none. |
| `note` | Free text attached when the record was made, or `null`. |

## Recorded runs

<!-- results:runs -->
| Run id | Label | Recorded | Orchestrator version | Replay ok |
| --- | --- | --- | --- | --- |
| run_d7e6c78b53b3 | hosted-default | 2026-11-11 | 0.1.0 | yes |
| run_378df0420b8c | hosted-ci-cheap | 2026-11-11 | 0.1.0 | n/a |
| run_41b058364191 | local-ollama | 2026-11-11 | 0.1.0 | n/a |
<!-- /results:runs -->
