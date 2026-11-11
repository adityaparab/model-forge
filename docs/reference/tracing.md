This page is the reference for tracing; the README is the front door.

# Tracing

Every process exports OpenTelemetry spans over OTLP/HTTP to the endpoints in `MODELFORGE_OTLP_ENDPOINTS`, a comma-separated list (Compose sets `http://jaeger:4318/v1/traces,http://phoenix:6006/v1/traces`). Unset, nothing is installed and nothing is exported, which is how the tests, CI and the CLI run by default. The services are `modelforge-worker`, `modelforge-api` and `modelforge-cli`.

**One trace per run.** A run gets a random trace id when it is created (`runs.trace_id`, replays included) and keeps it, so the worker's executions between gates, the resumes and the pauses of one run land in one trace. The run's console header links to it in Jaeger (`<jaeger_url>/trace/<trace_id>`) and to the `modelforge` project in Phoenix, which has no stable link to a single trace.

| Span | Where | Main attributes (prefix `modelforge.`) |
| --- | --- | --- |
| `run.execute` | one per worker execution | `run_id`, `queue_kind`, `worker_id`, `profile`, `replay_of`, `prior_status`, `final_status` |
| `node.<name>` | one per node attempt | `run_id`, `step_id`, `node`, `attempt`, `status`, `input_state_hash`, `output_state_hash` |
| `tool.<server>.<tool>` | one per MCP tool call attempt | `run_id`, `step_id`, `server`, `tool`, `version`, `attempt`, `duration_ms`, `input_hash`, `output_hash`, `artifact_ids`, `error_code` |
| `llm.<purpose>` | one per gateway attempt, schema retries included | `run_id`, `step_id`, `section_id`, `purpose`, `provider`, `model`, `effort`, the prompt's name, version and hash, `request_hash`, `attempt`, the four token counts, `cost_usd`, `latency_ms`, `stop_reason`, `schema_valid`, `error_kind` |
| HTTP request | the API | the FastAPI instrumentation's attributes, plus `modelforge.run_id` on the run routes; `/health` and the event streams are not traced |

The spans also carry the OpenInference attributes Phoenix reads (`openinference.span.kind` of `AGENT`, `CHAIN`, `TOOL` or `LLM`, `llm.model_name`, `llm.provider`, the `llm.token_count.*` counts and `llm.invocation_parameters`), and every span's resource names the Phoenix project `modelforge`. Jaeger shows the run as a tree of nodes, tool calls and LLM calls; Phoenix shows the same LLM spans with model, tokens, latency and cost.

**No content by default.** A span never carries a prompt, a message, an excerpt, a brief, a passage, an artifact value or a dataset value. A failure is recorded as an `exception` event with the exception type only, plus the gateway's error kind or the tool's error code as an attribute, never the message or the stack trace, because those can quote inputs. `MODELFORGE_TRACE_CONTENT=1` adds the system and user messages as sent (after the redaction scan passed) and the raw completion to each `llm.*` span, as OpenInference `llm.input_messages`, `llm.output_messages`, `input.value` and `output.value`, and lets the node and LLM spans record a failure with its message (tool spans never do). This is an exception to the project's data-handling rules: with it on, prompts and completions, which carry artifact excerpts, passages and the brief, leave the process for the configured collectors. It is for local collectors only. The gateway logs a warning at startup when it is on, and `/config` and the Settings page show the flag.

[Back to the README](../../README.md)
