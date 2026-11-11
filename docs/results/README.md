# Results

Every number the documents publish about a run comes from `runs.json` in this directory. A record is written by a script from a sealed run's eval report and its lineage; no metric is typed by hand, and no document states a measured number outside a results block.

## How a record is produced

```bash
uv run python scripts/record_result.py add <run_id> --profile <profile> --label <label>
```

`add` runs the eval harness against the existing sealed run (`modelforge-evals run --profile <profile> --run-id <run_id> --layers pipeline,retrieval`, which makes no LLM call) and writes `evals/reports/<profile>-<run_id>.json`. It then reads that report, the run record, its approvals and its replays from lineage and writes one record into `runs.json`, keyed by `run_id`: recording the same run again replaces its record. `--report <path>` uses an existing report instead of running the harness, and `--note <text>` attaches a note. The file is JSON with sorted keys and a trailing newline so diffs stay small.

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
| `findings` | Findings in total, by source (`precheck`, `review`), by severity (`high`, `medium`, `low`), and still open at sign-off. |
| `rounds` | Challenge and remediation rounds run. |
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
| no records yet |  |  |  |  |
<!-- /results:runs -->
