# ModelForge

ModelForge is a governed, multi-agent copilot for the credit risk model development lifecycle. Given a raw credit dataset and a modeling brief, it produces a validated probability-of-default (PD) model, an SR 11-7 style model development document, an independent "effective challenge" review, a tamper-evident audit trail, and a model inventory entry, with human approval gates at the points where a bank would require them.

The core toolkit (`packages/core`, `modelforge_core`) is the deterministic, LLM-free layer underneath. It loads a dataset into a canonical schema, profiles it and screens for leakage, builds train / validation / out-of-time splits, fits monotone WoE binning and IV / correlation feature selection, trains a WoE logistic scorecard champion and a LightGBM challenger, calibrates the champion, and validates it: discrimination (AUC, KS, Gini with bootstrap intervals), calibration (per-grade binomial, Hosmer-Lemeshow, expected / observed), stability (PSI, CSI), sensitivity (coefficient signs, WoE monotonicity, bin shifts), and a champion-challenger benchmark. The champion is refit iteratively, dropping any feature whose coefficient has the wrong sign, and these drops are recorded in the feature selection artifact. Every result is checked against documented thresholds into a single validation report. Models are stored as JSON (scorecard) and LightGBM native text (challenger); nothing is pickled.

The orchestrator (`packages/orchestrator`, `modelforge_orchestrator`) turns that toolkit into a governed run. A YAML brief starts a LangGraph run whose nodes call the toolkit only through MCP servers (`packages/mcp`). Every input and output is a content-addressed artifact, and every step, tool call and decision is an event in a hash-chained lineage store in Postgres (`packages/lineage`). The run stops at three human gates (scope, model, sign-off), with four-eyes checks on the last two, and a sealed run can be replayed to reproduce every deterministic artifact hash. This is a reference implementation, not a production system: sub-project 3 added the LangChain model gateway, through which the scope and documentation agents run, and sub-project 4 added an independent challenger agent, a bounded remediation loop between the challenger and the documentation agent, and an eval harness. Sub-project 5 put a control plane over the run: an HTTP API (`packages/api`, `modelforge_api`), a web console (`console/`) served by that API, and OpenTelemetry traces exported to Jaeger and Arize Phoenix.

## Quick start

```bash
make demo        # Docker Compose: Postgres, Jaeger, Phoenix, the API and the worker, plus one demo run
```

`make demo` builds the image (the console is built inside it), starts the five services, waits until `GET /api/v1/health` answers `ok`, submits `examples/briefs/polish-pd.yaml` under the `fake` profile as `alice` through the API, and prints the run id and its console URL, opening it in the browser when it can. It needs Docker, `curl` and `python3` on the host and nothing else, and it calls no model. The worker downloads the dataset into `./data` on first use, so the first run takes a little longer.

| What | Where |
| --- | --- |
| Console | `http://localhost:8000` |
| API documentation (OpenAPI) | `http://localhost:8000/docs` |
| Jaeger | `http://localhost:16686` |
| Phoenix | `http://localhost:6006` |

In the console, type an identity in the top bar (`alice`), approve the scope gate, then switch the identity to `bob` to accept the model and approve the sign-off: the `model` and `signoff` decisions must come from someone other than the requester. Once the run is sealed the worker replays it on its own and the run's Overview shows the reproducibility badge. `make down` (or `make demo-down`) stops the stack; `docker compose down -v` also deletes the database volume. If port 5432 is taken by another Postgres, `MODELFORGE_POSTGRES_PORT=5433 make demo` publishes the Compose one elsewhere (the API and the worker reach it inside the Compose network either way).

To work on the code:

```bash
make sync        # uv sync --all-groups and npm ci --prefix console
make lint test   # ruff, pyright, import-linter, the console's typecheck and lint, then the Python suite and the console tests
make example     # end-to-end run on the Polish data, out-of-time vintage 5
```

`make example` downloads the dataset into `data/` on first use, prints one line per validation test plus the overall status, and writes `scorecard.json`, `gbm_model.txt`, `validation_report.json`, `binning_tables.json`, `column_dictionary.json` and `profile.json` under `out/polish_bankruptcy/`. For the second dataset:

```bash
uv run python examples/core_pipeline.py --dataset taiwan_bankruptcy --oot-vintages ""
```

Other flags: `--data-dir` (default `data`), `--out` (default `out`), `--seed` (default 42), `--max-features` (default 20).

The slow tests read the cached datasets from `data/` at the repository root; set `MODELFORGE_DATA_DIR` to point them at another directory (for example a shared cache).

## Run a governed model build

```bash
make db-up                    # Postgres via Docker Compose
uv run modelforge migrate     # lineage tables and LangGraph checkpoint tables
uv run modelforge run examples/briefs/synthetic.yaml --requester alice
```

`run` creates the run, executes it in the foreground to the first gate and prints `<run_id> <status>` (here `awaiting_scope`). Each `approve` records the decision and continues to the next gate or the end:

```bash
uv run modelforge approve <run_id> scope --decision approve --approver alice
uv run modelforge approve <run_id> model --decision accept --approver bob
uv run modelforge approve <run_id> signoff --decision approve --approver bob
uv run modelforge status <run_id> --json   # run, steps and artifact refs with summaries
uv run modelforge replay <run_id> --json   # rerun and compare deterministic artifact hashes
uv run modelforge replay <run_id> --rerun-llm --json   # also call the agents again
uv run modelforge verify <run_id>          # walk the event hash chain, artifacts and manifest
```

`examples/briefs/polish-pd.yaml` runs the same sequence on the Polish data. Gate decisions:

| Gate | Decisions |
| --- | --- |
| `scope` | `approve`, `edit`, `reject` |
| `model` | `accept`, `adjust`, `reject` |
| `signoff` | `approve`, `approve_with_conditions`, `reject` |

`edit` and `adjust` take a modeling plan with `--plan`; `approve_with_conditions` takes one or more `--condition`. The `model` and `signoff` decisions must come from someone other than the requester, and `approve` at `signoff` is refused while a critical or high finding of the challenger is open or disputed (see "Remediation loop"). Run statuses are `queued`, `running`, `paused`, `awaiting_scope`, `awaiting_model`, `awaiting_signoff`, `needs_attention`, `completed`, `rejected` and `aborted`. A run that fails a tool call parks in `needs_attention`; `uv run modelforge resume <run_id>` retries it from its last checkpoint and `uv run modelforge abort <run_id>` ends it. A run parked by its cost cap resumes with a higher cap: `uv run modelforge resume <run_id> --cost-cap 20` sets the cap in USD before re-enqueueing (allowed from `needs_attention` and `paused`). `run`, `approve`, `resume` and `replay` accept `--detach` to only enqueue and leave execution to a worker (`uv run modelforge worker`).

**Pause.** `uv run modelforge pause <run_id>` (or the console's Pause button) asks a `running` run to stop at its next node boundary; it is refused in any other status. The worker parks the run in `paused` before it starts the next node, writes a `run.paused` event naming that node and releases its queue row; `resume` continues from the last checkpoint and `abort` ends it. The three gate nodes and `seal` ignore the request (a gate resumes an interrupt that must not be split from its step; `seal` takes seconds). There is no cancellation of a running run: `abort` is refused while `running`, so pause first, then abort. A pause request that arrives too late (the run parks at a gate, fails or finishes first) is cleared, never carried into a later execution.

The run manifest records every artifact hash, and `replay` reproduces each deterministic artifact byte for byte. A plain replay does not call a model: it copies the original's modeling plan, scope notes and final document sections and re-renders the document, so the document refs match too, and it copies the original's findings and responses instead of running the challenger and the remediation loop. `--rerun-llm` runs the scope, documentation, challenger and remediation agents again; the rendered document, completeness, findings and responses refs are then listed under `regenerated` instead of being compared, and `ok` ignores them. `verify` exits non-zero if an event or artifact has been altered.

**Replay from the console and on seal.** A sealed run's header offers Replay and Replay with `--rerun-llm`; both create the replay run and leave it to the worker, and the Overview's replays panel shows each replay with its report. When the `auto_replay` setting is on, the worker creates a plain replay of every non-replay run that completes (the replay's `run.created` event carries `{"auto": true}`; a replay is never replayed again), and the panel shows "Reproduced: N artifacts matched" once it seals, or the mismatched refs. A failure to create the automatic replay is logged and leaves the original run as it is. The setting is off by default and on in Compose.

**Through the API and the console.** The same sequence runs over HTTP with the worker executing: `POST /api/v1/runs` creates and enqueues the run, `POST /api/v1/runs/{id}/approvals` records a decision and enqueues the resume, and the console's gate panel posts the same request. Every write names its actor in the `X-ModelForge-User` header (1 to 64 characters of `[A-Za-z0-9._@-]`); the console sends whatever identity is typed in its top bar. The approver of a gate is that identity, and the four-eyes rule and the sign-off rule are enforced by lineage exactly as for the CLI, so a refusal comes back as `409` with the same message (and, for the sign-off rule, the blocking findings), which the console shows inline. With the stack from `make demo` running:

```bash
curl -s -X POST 'http://localhost:8000/api/v1/runs?profile=fake' \
  -H 'X-ModelForge-User: alice' -H 'Content-Type: application/yaml' \
  --data-binary @examples/briefs/synthetic.yaml
curl -s -X POST http://localhost:8000/api/v1/runs/<run_id>/approvals \
  -H 'X-ModelForge-User: alice' -H 'Content-Type: application/json' \
  -d '{"gate": "scope", "decision": "approve"}'
```

The header is an identity, not authentication: anyone who reaches the API can send any name. `MODELFORGE_USERS` adds an optional role map, a space-separated list of `name:role[,role]` entries such as `alice:requester bob:approver carol:admin,approver`. `requester` may submit a run; `approver` may decide a gate, pause, resume, abort and replay; `admin` may change the server-side settings. Unset (or empty), every identity has every role, which is what the demo and the tests use. A write by an identity without the role is `403`; a missing or malformed header is `401`. A malformed entry, an unknown role or a name listed twice is a configuration error at startup. The CLI keeps `--requester` and `--approver`.

**Server-side settings.** Three defaults live in the database rather than the environment, so they can be changed while the stack runs: `default_profile` (the profile a run submitted without one gets), `default_cost_cap_usd` (its cost cap) and `auto_replay`. They are seeded from `MODELFORGE_PROFILE`, `MODELFORGE_COST_CAP_USD` and `MODELFORGE_AUTO_REPLAY` the first time `modelforge migrate`, the worker or the API starts against a database, and only when the key is absent; after that the database value wins for the API and the worker. The CLI's `run` keeps its environment defaults. The console's Settings page (or `PATCH /api/v1/settings`, which needs the `admin` role under a role map) edits them; every change writes an audit row with the old and new value and who made it, and the page lists the last 20. A profile must name a file in the profiles directory and the cap must be positive. Identity and tracing stay environment-only.

**Costs across runs.** The console's Costs page (and `GET /api/v1/costs?since&until`, default the last 30 days) totals the `llm_calls` rows of a window: spend, runs, calls and tokens, by day, by run (cost against cap), by profile, by model and by purpose. Every run created in the window is listed, with zero cost when it made no call, so `fake` runs count at 0 USD.

## Console

The console (`console/`, React 19, Vite, TypeScript, TanStack Query, Tailwind CSS) is a dark operations view over the API, served by the API itself from `/`. Pages:

| Page | Path | What it shows |
| --- | --- | --- |
| Runs | `/runs` | every run with model name, status, profile, requester, cost against cap, findings and the highest open severity, replays marked; filters by status, profile and requester and a toggle to hide replays; updated live |
| New run | `/runs/new` | an example brief picker (from `examples/briefs`) that fills a YAML editor, the profile and the cost cap (both defaulting to the server-side settings); the server's `400` message shown inline |
| Run detail | `/runs/:id` | a header with status, cost, trace links and the actions the status allows (pause, resume with an optional new cap, abort, replay), and six tabs: Overview (the fourteen-node timeline with attempts, durations and call counts, the live event feed, the gate panel with its decision form, the `needs_attention` panel, approvals, replays and the reproducibility badge), Document (sections with a grounding badge each and hovers that resolve every `[A:]` marker to its artifact value and every `[K:]` marker to its passage; HTML and Markdown downloads), Validation (the thirteen tests and the thresholds), Findings (grouped by the round that raised them, with responses and verdicts), Lineage (the artifacts, the event hash chain with a verify button, the manifest), LLM calls (every attempt and the cost by purpose and model) |
| Inventory | `/inventory` | the model inventory entries with links to their runs |
| Costs | `/costs` | spend across runs over 7, 30 or 90 days |
| Settings | `/settings` | the identity and the role map, the editable defaults with their audit list, and the read-only configuration: profiles with routes and prices, directories, tracing endpoints and the content flag, versions |

The identity typed in the top bar is kept in the browser's local storage and sent as `X-ModelForge-User` on every write; reads need none. Run pages follow the run's event stream and refetch what changed, and the Runs page follows the global stream; the top bar shows whether the stream is `live`, `reconnecting` or `offline`.

**Building.** The Docker image builds the console in a Node stage and the API serves it from `MODELFORGE_CONSOLE_DIR`. Outside Docker, `make console` builds it into `console/dist` (git-ignored), which `make api` then serves at `http://localhost:8000`; the API needs Postgres (`make db-up`) and runs execute only while a worker runs beside it (`make worker`). Without a build the API answers `/` with a short page naming `make console` and `make ui-dev`.

**Development mode** is opt-in. `make ui-dev` runs the Vite dev server (on Vite's default `http://localhost:5173`) with hot reload and proxies `/api` to the API on `http://localhost:8000`, so run `make api` (or the Compose stack) beside it and open the Vite URL. Setting `MODELFORGE_CONSOLE_DEV_URL=http://localhost:5173` for the API makes it redirect `/` and every other console path there (307), so links into `:8000` land on the dev server too.

**Types and fixtures.** `console/src/api/types.gen.ts` is generated from the API's OpenAPI document: `make console-types` writes `console/openapi.json` with `modelforge-api openapi` (no database needed) and regenerates the types from it. Both files are committed and CI fails when `make console-types` changes them. `console/src/fixtures/*.json` are the read endpoints' responses for one completed synthetic `fake` run, the console tests' data; `make console-fixtures` drives that run on a temporary database (it needs Postgres) and rewrites them, with directories written as `<dir>` and artifact URIs as `artifact://<id>` so the files carry nothing machine-specific. Refresh them by hand when an endpoint's shape changes. `make console-test` runs the console tests (Vitest and React Testing Library over the fixtures); `make test` includes them.

## API

The API (`packages/api`, `modelforge_api`, the `modelforge-api` command) is a FastAPI service over the lineage store. It never executes the graph and never calls a model: a write records lineage rows and enqueues, and the worker executes, exactly as the CLI's `--detach`. It applies the migrations and seeds the settings at startup. `modelforge-api` serves it (`serve` is the default command, with `--host`, `--port` and `--reload`); `modelforge-api openapi` prints the OpenAPI document; the interactive documentation is at `/docs`. Every path below is under `/api/v1`.

| Read | Returns |
| --- | --- |
| `GET /health` | `{status, database, version}`; `503` when the database query fails |
| `GET /config` | the redacted effective configuration: default profile, console mode, identity mode and roles, trace UI links, tracing endpoints and the content flag, directories, versions, the graph's nodes and the gates with their decisions |
| `GET /briefs` | the example briefs of `MODELFORGE_BRIEFS_DIR` with their text |
| `GET /runs` | runs newest first, with `limit` (1 to 200, default 50), `offset`, `status`, `profile`, `requester` and `replays=include|exclude` |
| `GET /runs/{id}` | the run, its refs, counters, the open gate and its summary, approvals, findings and responses summaries, the cost summary, the inventory entry, the trace links and the replays |
| `GET /runs/{id}/steps` | the steps in order, each with its duration, tool calls and LLM calls |
| `GET /runs/{id}/llm-calls` | the `llm_calls` rows and the cost summary by purpose and model |
| `GET /runs/{id}/artifacts`, `GET /runs/{id}/artifacts/{artifact_id}` | the run's artifacts with the ref names that point at each; one artifact's content (JSON, text, or a download) |
| `GET /runs/{id}/document?format=sections|html|md` | the sections with titles, grounding and resolved citations (default), or the stored HTML or Markdown |
| `GET /runs/{id}/validation` | the validation report |
| `GET /runs/{id}/findings` | the findings, the remediation responses and a summary |
| `GET /runs/{id}/approvals`, `GET /runs/{id}/manifest`, `GET /runs/{id}/verify` | the approvals; the sealed manifest; the result of verifying the event chain, artifacts and manifest |
| `GET /runs/{id}/events?after&limit` | a page of the run's events after an event id |
| `GET /runs/{id}/replays` | the run's replays, oldest first, each as `{run, report}` (the report `null` until the replay seals) |
| `GET /inventory`, `GET /inventory/{model_id}` | the model inventory; one entry |
| `GET /costs?since&until` | costs across runs (see "Costs across runs") |
| `GET /settings` | the editable settings, the last 20 audit rows, the configuration and the profiles |

| Write | Role | Effect |
| --- | --- | --- |
| `POST /runs` | `requester` | JSON `{brief, profile?, cost_cap_usd?}`, or the brief YAML as the body (`Content-Type: application/yaml`) with `profile` and `cost_cap_usd` as query parameters; `201` with the run; `400` for a brief that does not parse or a profile that is unknown or lacks its credential |
| `POST /runs/{id}/approvals` | `approver` | `{gate, decision, comment?, plan?, adjust_target?, conditions?}`; `202`; `409` for a decision lineage refuses, or while a decision at this gate is already queued |
| `POST /runs/{id}/resume` | `approver` | `{cost_cap_usd?}`, from `needs_attention` or `paused`; `202` |
| `POST /runs/{id}/pause` | `approver` | from `running` only; `202` (the status changes at the next node boundary) |
| `POST /runs/{id}/abort` | `approver` | refused while `running` (pause first) and once final; `200` |
| `POST /runs/{id}/replay` | `approver` | `{rerun_llm?}`, for a sealed run; `202` with the replay run |
| `PATCH /settings` | `admin` | any of `default_profile`, `default_cost_cap_usd`, `auto_replay`; returns the new settings document |

A state change the run's status does not allow is `409`. Pause is one conditional update that succeeds only while the run is `running`; abort and the worker's claim change the status with one guarded update that names the statuses it may move from; the approvals and resume enqueue the resume only when none is queued. So a second gate decision while one is waiting is refused with `409`, and an abort racing a claim is never lost.

**Errors.** Every error the API writes has one shape, `{"error": {"kind": ..., "message": ...}}`, sometimes with extra fields (the blocking findings of a sign-off refusal, the key of a refused setting, the validation details of a `422`). The kinds are `identity` (401), `role` (403), `not_found` (404), `not_ready` (409, a run without a document, validation report or manifest yet), `brief`, `profile` and `settings` (400), `approval` and `state` (409), `validation` (422, or 400 for a `since` after `until`) and `internal` (500, the exception type only, never a traceback).

**Event streams.** `GET /runs/{id}/events/stream` and `GET /events/stream` are server-sent event streams of lineage events. Each message carries `id:` (the event id) and `data:` (the event record as JSON) and no `event:` line, so a browser `EventSource` delivers every message to `onmessage`; read the event type from `data.type`. A stream resumes after the `Last-Event-ID` header or an `?after=<id>` parameter; without either, the run stream starts at the run's first event and the global stream at the current head. The API polls the events table every 0.5 s and sends a keepalive comment after 15 s of silence; there is no `LISTEN`/`NOTIFY`. The run stream is exact, because a run's events are written one at a time. Across runs a lower event id can commit after a higher one was already sent, so the global stream re-reads a trailing window of 200 ids behind its cursor and skips ids it has sent; an event that commits more than 200 ids out of order is missed, and a reconnect may repeat events. The console uses the global stream only to refresh the run list, so neither matters there.

```bash
curl -N http://localhost:8000/api/v1/runs/<run_id>/events/stream
```

## Tracing

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

## MCP servers

The toolkit is exposed through five MCP servers over stdio: `data-profiler`, `credit-modeling`, `validation`, `doc-renderer` and `knowledge`. Each runs with:

```bash
uv run modelforge-mcp <server>
```

They read `MODELFORGE_ARTIFACT_DIR` (the artifact store) and `MODELFORGE_DATA_DIR` (the dataset cache); `doc-renderer` also reads `MODELFORGE_TEMPLATES_DIR`, and `knowledge` reads its corpus from `MODELFORGE_KNOWLEDGE_DIR`. To use a server from a desktop MCP client, add it to the client's configuration, for example:

```json
{
  "mcpServers": {
    "modelforge-credit-modeling": {
      "command": "uv",
      "args": ["run", "--project", "<repo>", "modelforge-mcp", "credit-modeling"],
      "env": {
        "MODELFORGE_ARTIFACT_DIR": "<repo>/artifacts",
        "MODELFORGE_DATA_DIR": "<repo>/data"
      }
    }
  }
}
```

The orchestrator starts the servers itself as `python -m modelforge_mcp <server>`. A custom `MODELFORGE_MCP_COMMAND` must accept a trailing `<server> --marker <uuid>` argument pair: the worker tracks the server processes it started by that marker. `make goldens` regenerates the golden artifacts.

## LLM gateway

Every model call goes through one gateway (`packages/gateway`, `modelforge_gateway`) built on LangChain chat models: `langchain-anthropic` for Anthropic, `langchain-ollama` for Ollama and `langchain-openai` pointed at LM Studio's OpenAI-compatible local server. No node imports a provider SDK. Each call names a purpose (`scope`, `document`, `challenge`, `remediate`, and later `judge`), and the run's profile routes that purpose to a provider, model and effort. The output is structured: the gateway validates it against a Pydantic schema and retries a failed validation at most twice.

Profiles live in `profiles/<name>.yaml` (`MODELFORGE_PROFILES_DIR`):

| Profile | Provider | Model | Effort (scope / document / challenge / remediate / judge) | Prices (USD per million tokens: input, output, cache read, cache write) |
| --- | --- | --- | --- | --- |
| `default` | `anthropic` | `claude-opus-5-5` | low / high / high / medium / medium | `claude-opus-5-5` 4.0, 20.0, 0.20, 5.0; `claude-sonnet-5-5` 2.0, 10.0, 0.20, 2.5 |
| `ci-cheap` | `anthropic` | `claude-sonnet-5-5` | low / medium / medium / low / low | same table as `default` |
| `local-ollama` | `ollama` | `gpt-oss:20b` | not set | none (local, costs 0) |
| `local-lmstudio` | `lmstudio` | `google/gemma-4-26b-a4b-qat` | not set | none (local, costs 0) |
| `fake` | `fake` | `scripted-v1` | low for every purpose | none (offline, costs 0) |

`MODELFORGE_PROFILE` selects the profile when a run is created; it is recorded on the run, and the worker uses the recorded profile for every later step, so a run never switches provider. `run` exits with code 2, before creating the run, on an unknown profile or one whose credential is missing. The Anthropic key is read from `ANTHROPIC_API_KEY`, normally kept in the git-ignored `.env` at the repository root:

```bash
ANTHROPIC_API_KEY=...
MODELFORGE_PROFILE=default   # optional
```

`MODELFORGE_ANTHROPIC_BASE_URL` redirects Anthropic traffic; the inherited `ANTHROPIC_BASE_URL` is never read. The local providers take `MODELFORGE_OLLAMA_BASE_URL` (default `http://localhost:11434`), `MODELFORGE_LMSTUDIO_BASE_URL` (default `http://localhost:1234/v1`), and `MODELFORGE_OLLAMA_MODEL` and `MODELFORGE_LMSTUDIO_MODEL` to override the profile's model. `MODELFORGE_OLLAMA_NUM_CTX` (default `32768`) sets Ollama's context window, whose server default is too small for the documentation prompts.

**Cost accounting.** Every attempt writes an `llm_calls` row with the purpose, provider, model, input, output and cache tokens, cost in USD, latency, the prompt's name, version and hash, and a reference to an `llm_response` artifact holding the raw response with its request hash. The run's running total is kept in Postgres and checked before each attempt, schema retries included: when what has been spent plus the projected cost of the next call would exceed the run's cap (10 USD unless `MODELFORGE_COST_CAP_USD` sets another value for new runs), nothing is sent and the run parks in `needs_attention`. `resume --cost-cap <usd>` raises the cap and continues. `status` prints a `cost: <spent> USD / cap <cap> USD` line, and `status --json` includes the `llm_calls` rows.

**Redaction.** Agents never see dataset rows or artifacts, only excerpts: per-kind projections that keep aggregates and drop row ids, bins, model text and the dataset's original column names. Before any call, the gateway scans every outbound message, schema-retry messages included, for e-mail addresses, IBAN-like tokens and runs of nine or more digits (not the digits of a decimal fraction or a `k_` citation id), and for restricted terms: the brief's optional `restricted_terms` on every call, and the dataset's original column names once the dataset is loaded, so on every documentation call. The scope prompt shows the brief without its `restricted_terms` list, so a brief that lists a term can still pass `scope`; the term anywhere else in the brief refuses the call. A hit refuses the call (it never masks), records the pattern name and message role but not the matched text, and parks the run.

**Prompts.** Prompts are versioned files under `prompts/<agent>/<name>.v<N>.md` (`MODELFORGE_PROMPTS_DIR`) with YAML front-matter and a Jinja2 body. The hash of each file is recorded in a `prompts` row and on every `llm_calls` row, so a document section traces to the exact prompt text. Editing a prompt without bumping its version is a configuration error.

**The `fake` profile.** `fake` is a scripted stand-in, not a recording of a real model. It reads the fenced blocks the prompts mark (the brief, the defaults, the artifact excerpts and the retrieved passages) and builds its answers deterministically from them: the modeling plan the brief implies, a fixed set of queries per section, and sections written from a script that copies numbers out of the excerpts with the same rounding rule the grounding check applies. The challenger and remediation calls are scripted the same way (see "Remediation loop"). It needs no network and costs nothing. It is the default profile for the test suite and for the Docker image, so everything runs offline; it shows that the pipeline, lineage and checks work, not how well any model writes.

## Knowledge layer

The agents cite a small corpus in `knowledge-base/` (`MODELFORGE_KNOWLEDGE_DIR`), served by the `knowledge` MCP server (`packages/knowledge`, `modelforge_knowledge`):

| id | Kind | What it is | Source |
| --- | --- | --- | --- |
| `sr11-7` | primary | SR 11-7, Supervisory Guidance on Model Risk Management (2011), full text; superseded, kept as the structure the development document follows | Federal Reserve Board and OCC, public domain |
| `sr26-2` | primary | SR 26-2, Revised Guidance on Model Risk Management (2026), letter and attachment, full text | Federal Reserve Board, FDIC and OCC, public domain |
| `occ-2026-13` | summary | The OCC bulletin transmitting the same revised guidance | Office of the Comptroller of the Currency; summary in our own words |
| `cecl-asc-326` | summary | CECL expected credit loss concepts relevant to PD | FASB ASU 2016-13 (Topic 326); summary in our own words |
| `ifrs9-ecl` | summary | IFRS 9 staging and lifetime expected credit loss | IASB, IFRS 9; summary in our own words |
| `basel-irb` | summary | PD, LGD and EAD under the internal ratings-based approach | Basel Committee, CRE30 to CRE36; summary in our own words |
| `modeling-standards` | internal | Required document sections, default thresholds, mandatory validation tests, the two dataset proxies | This project |
| `modelforge-governance` | internal | How this system governs itself: gates, four-eyes rule, lineage, replay, redaction | This project |

The corpus is chunked by heading and indexed with BM25 (no embeddings). Each chunk has a stable citation id, `k_` followed by twelve hex characters of a hash of its document, section and text. The `knowledge` server has three tools: `search_regulation` (ranked passages for a query, optionally restricted to some documents), `get_passage` (one passage by citation id) and `get_index` (the stored index). The index is a content-addressed `knowledge_index` artifact pinned in every run manifest, so a replay compares it like any deterministic artifact and detects a changed corpus.

**Citations.** Generated text cites with two markers: `[A:<artifact_id>#<path>]` for a value in a run artifact and `[K:<citation_id>]` for a knowledge passage (the agent writes an alias such as `[A:discrimination#by_split.oot.auc]`, which the node expands to the artifact id). A path is a sequence of dotted keys. A step applied to a list selects the element whose discriminator equals the step, trying `split`, `test_id`, `feature`, `grade` and `name`; a discriminator that itself contains dots is written in brackets, as in `tests[disc.auc.oot].status`; and `.count` gives a list's length. The same grammar resolves on the excerpt the agent saw and on the stored artifact.

**Grounding checks.** Every drafted section is checked before it is stored. Every number in a sentence must match a value cited by an `[A:]` marker in that sentence, at the number's own precision (a value written to three significant figures is compared after rounding to three); every marker must resolve; and a sentence that names a regulation must carry a `[K:]` marker naming a passage retrieved for that section. A section that fails is regenerated once with the failures fed back to the model; if it still fails, it is stored with a visible `REQUIRES AUTHOR ATTENTION` banner listing the failures, and the `document_sections` summary counts it in `n_flagged_sections`. Nothing is silently dropped.

**Regulatory framing.** SR 26-2 superseded SR 11-7 and SR 21-8 on 17 April 2026, and it places generative and agentic AI outside its scope, while broader risk management expectations still apply to such tools. This project keeps SR 11-7's document structure for the development document, because that is the structure model validators know, and governs its own agents anyway: gates, four-eyes approval, lineage, cost caps, redaction and grounding apply to every model call even though the guidance does not require them.

## Challenger review

The `challenge` node is an independent reviewer inside the run: it did not build the model and did not write the document, and it runs after `document` and before the sign-off gate.

**What it sees.** Its model context is assembled in one place (`challenger.py`) from lineage, by artifact id: the rendered document, the validation report through the same excerpt projection the documentation agent gets, the modeling plan read from the run's recorded plan artifact (`runs.plan_ref`, not the in-memory run state), and the knowledge passages its own queries retrieve. The pre-checks below also read the completeness report and, by id, the artifacts the document's markers cite; neither is sent to the model. The challenger never reads the brief, the scope notes, the section drafts and their grounding reports, a raw model response or the column dictionary. `test_challenger_isolation.py` proves it: the test plants a distinct sentinel string in each of those developer-side artifacts (the brief's constraints, the scope rationale and open questions, a superseded section draft and its grounding report, a stored raw response, a column description), runs `challenge` through a recording gateway and a recording store, and asserts that no sentinel appears in any message of any challenge call and that every artifact the node read is of an allowed kind or cited by the document. Its calls use their own purpose, `challenge`, so a profile can route the reviewer to a different model from the developer agents; the shipped profiles route it to the same model. Redaction applies to its calls as to every other.

**Deterministic pre-checks.** Every challenge round first runs seven checks over the rendered document, before any model call. Each failing check becomes a finding with a stable key built from ids and ordinals, so a later round can close it when the check stops firing:

| Key | Severity | Fires when |
| --- | --- | --- |
| `missing_section:<id>` | `critical` | a required template section is absent or holds only the author-attention banner, or the completeness report lists it as missing |
| `bannered_section:<id>` | `high` | a section starts with the `REQUIRES AUTHOR ATTENTION` banner above further content |
| `undiscussed_test:<test_id>` | `high` for `fail`, `medium` for `warn` | a failing or warning validation test is neither cited by a marker nor named anywhere in the document (text inside the banner block or a code span does not count) |
| `unaddressed_na:<test_id>` | `high` | a not-applicable test (such as an out-of-time or sensitivity test on data without vintages) is not named in a sentence that says it is not applicable, not available, n/a, not assessed, could not be assessed or unavailable |
| `unreported_test:<test_id>` | `medium` | a passing out-of-time (`disc.*.oot`) or sensitivity (`sens.*`) test is neither cited by a marker nor named anywhere in the document |
| `reverify:<id>` | `high` | the grounding check, re-run over the section against the sealed artifacts and the knowledge index, finds an ungrounded number, an unresolved marker, an uncited regulatory sentence or an unknown passage; a bannered section is left to `bannered_section` |
| `miscited_regulation:<id>:<n>` | `medium` | sentence `n` of the section names a regulation and cites a known passage that neither belongs to that regulation's document nor names it (an unknown passage is left to `reverify`) |

The `reverify` check is the documentation agent's grounding check run again from the reviewer's side, so a number changed after the section was written is caught without a model.

**The review.** In round 1 the challenger then makes two calls. A query-planning call sees the template's section titles, the plan's regulatory scope, the status of each validation test and the pre-check findings, and returns up to five queries; each query fetches the top three passages from the knowledge corpus. The review call sees the rendered document, the validation excerpt, the plan, the retrieved passages and the pre-check findings (so it does not repeat them), and returns findings, each with a severity, a category (`conceptual_soundness`, `data`, `methodology`, `performance`, `documentation`, `governance`, `limitations` or `monitoring`), a title, a description, evidence and one required action.

**Evidence grammar.** Each evidence item is exactly one of: a template section id (`performance`); an artifact marker `[A:validation#<path>]` or `[A:plan#<path>]`, whose path follows the citation path grammar of the knowledge layer and must resolve in that excerpt (no other alias is allowed); or a passage marker `[K:<citation_id>]` naming a passage retrieved for this review. A finding with no evidence, or with an item outside the grammar, is rejected by a validation hook and the gateway retries with the problems listed, as for a schema failure, then parks the run. Stored evidence has its aliases expanded to artifact ids.

**Severities.** The system prompt binds the model to these meanings, and the pre-checks use the same scale:

- `critical`: a required section or validation result is missing and the document cannot be relied on.
- `high`: a material weakness in data, method, performance, or an ungrounded statement, that must be fixed before use.
- `medium`: a weakness to fix or to accept as a sign-off condition.
- `low`: editorial.

**Findings.** Findings get ids `F-1`, `F-2`, ... in the order they are raised, pre-check findings before review findings, numbered on across rounds. Each records its source (`precheck` or `review`), the round that raised it, its status (`open`, `accepted`, `disputed` or `closed`), the developer's response and evidence, the challenger's verdict and the round that closed it. The `findings` artifact is cumulative: every round stores the whole list with current statuses, so the sealed manifest's `findings` ref is the full history. Its summary carries `n_findings`, `max_severity` (over every finding, closed ones included), `max_open_severity` (over the findings still open or disputed, null when none), `by_severity`, `by_status`, `by_source` and `round`, and the sign-off gate's summary shows it with the remediation responses. Each finding is also a row of the `findings` table, keyed `<run_id>/F-<n>`. `status` prints a line after the cost line once a run has findings, for example

```
findings: 6 (open 0, accepted 0, disputed 1, closed 5; max severity high)
```

and `status --json` includes the rows.

## Remediation loop

When a challenge round leaves a finding `open` and the bound allows another round, the run goes to `remediate`, where the developer side answers.

**Responses.** `remediate` makes one call per open finding (purpose `remediate`). The call sees the finding, the current bodies of the sections its evidence names (every section when it names none), and the plan and artifact excerpts those sections may cite. It accepts the finding, with a concrete action and the template sections to regenerate, or disputes it; a dispute must carry at least one `[A:<alias>#<path>]` marker that resolves in those excerpts. A validation hook enforces both rules and rejects unknown section ids. The finding becomes `accepted` or `disputed`, with the response and its evidence recorded on the finding and its row.

**Regeneration.** The union of the sections the accepted findings name is rewritten through the documentation agent's own write path (query planning, retrieval, writing, the grounding check, one regeneration and the banner on a second failure), under the remediation prompts and with the findings that name each section attached in a block of their own. The node stores a new `document_sections` (the untouched sections copied), re-renders the document, checks its completeness again, and stores a cumulative `responses` artifact (summary: `n_responses`, `n_accepted`, `n_disputed`, `n_sections_regenerated`, `round`) and the findings with their new statuses. A round that regenerates nothing re-renders nothing. The run then returns to `challenge`.

**Re-review.** Every later challenge round re-runs the pre-checks over the re-rendered document and settles their findings without a model: a pre-check finding whose check no longer fires closes (verdict "check passes after remediation"), one whose check still fires is `open` again (a disputed one stays `disputed`), and a check that fires with no live finding of its key raises a new pre-check finding. One re-review call then receives the review findings the developer accepted or disputed, with their responses, the sections regenerated in the last remediation round with their new bodies, and the validation and plan excerpts, and must return exactly one verdict per finding (`closed`, `open` or `disputed`, with a note); no call is made when there are none. The re-review raises no new model findings.

**The bound.** The brief's optional `remediation_rounds` (default 2, from 0 to 3; 0 means review only) caps the remediation rounds. `challenge` routes to `remediate` while some finding is open and fewer rounds than the bound have run, otherwise to the sign-off gate, so the longest path at the default is `document -> challenge -> remediate -> challenge -> remediate -> challenge -> gate_signoff`. A round that leaves nothing open exits early, and whatever is still open when the bound is reached goes to the gate as it is.

**Sign-off rule.** `approve` at the `signoff` gate is refused while any finding of the run with severity `critical` or `high` is `open` or `disputed`. The CLI prints the refusal, naming each blocking finding's id, severity and title, and exits 2; `approve_with_conditions` and `reject` stay available, so a reviewer can sign off with the findings as conditions or send the model back. Medium and low findings, and disputed ones below high, never block.

**Replay.** A plain `replay` makes no model call: the challenger and remediation nodes copy the original's findings and responses with their final statuses, the document node copies the final, remediated sections, and the deterministic refs, the document included, are compared as before. `replay --rerun-llm` runs the challenger and the remediation loop again with the other agents, and lists the rendered document, completeness, findings and responses refs under `regenerated`.

**Under `fake`.** The `fake` profile's findings and responses are scripted. Its review always raises two findings: a medium monitoring finding ("Monitoring thresholds are not quantified", citing the monitoring section and the plan's PSI warning threshold) and a low governance finding ("Approver identities are not named in the governance section"). It adds a medium data finding when the plan's target names a proxy that the limitations section does not mention, and a high performance finding for each performance sentence that claims a test "was performed" without a marker. The scripted developer always disputes the governance finding (approvals are recorded in lineage, not in the document body) and any proxy finding, citing the plan, and accepts every other finding, regenerating the sections its evidence names; the scripted re-review closes an accepted finding whose section was regenerated and keeps disputes disputed. This shows that the loop, its bound, its lineage and the sign-off rule work, not how well any model reviews a document.

## Evals

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

## Running against a real model

Each live profile runs the same governed sequence; only the profile changes.

```bash
# Anthropic: ANTHROPIC_API_KEY in .env
MODELFORGE_PROFILE=default uv run modelforge run examples/briefs/polish-pd.yaml --requester alice

# Ollama on localhost:11434
ollama pull gpt-oss:20b
MODELFORGE_PROFILE=local-ollama uv run modelforge run examples/briefs/polish-pd.yaml --requester alice

# LM Studio serving google/gemma-4-26b-a4b-qat on port 1234
MODELFORGE_PROFILE=local-lmstudio uv run modelforge run examples/briefs/polish-pd.yaml --requester alice
```

Then approve the three gates as in "Run a governed model build". If the challenger leaves a critical or high finding open or disputed, the sign-off gate refuses `approve` and names the findings; sign off with `approve_with_conditions` or `reject` instead. `MODELFORGE_OLLAMA_MODEL` and `MODELFORGE_LMSTUDIO_MODEL` run a different local model under the same profile, and the base URL variables point at another host. Ollama runs with a 32768-token context window unless `MODELFORGE_OLLAMA_NUM_CTX` sets another; pick one the model and the machine's memory support.

Every provider returns structured output through its integration's `json_schema` method by default, Anthropic included (forced tool calling is unsupported when thinking is enabled). A route's `structured` key overrides that: the Anthropic profiles set `structured: function_calling` on the `scope` route, because the modeling-plan schema exceeds the structured-output grammar's complexity limit, while the document routes keep `json_schema`. A section-writing call may use up to 8192 output tokens and a query-planning call up to 1024; on a thinking model the thinking tokens count against that limit.

Results on the Polish brief through the three gates, on one machine (Apple Silicon, 48 GB), with the deterministic artifacts identical across every run (out-of-time AUC 0.874, overall validation `fail`, 13 features). The `fake` row's findings, rounds, regenerated sections and LLM calls were measured on 10 November 2026 after the final-review fixes (its wall time at commit `5a11843`), with the challenger and the remediation loop; the `default` row was measured through the eval harness on 10 November 2026 at the head of this branch, and the `ci-cheap` profile has not been run. The four rows below them were measured on 10 November 2026 at commit `00e0c6b` and predate the challenger and the remediation loop, so their findings, rounds and regenerated-section columns read n/a:

| Profile | Model | Outcome | Flagged sections | Ungrounded numbers | Findings (pre-check / review) | Rounds (challenge / remediation) | Sections regenerated | LLM calls | Cost (USD) | Wall time |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `fake` | `scripted-v1` | completed; signed off with `approve` | 0 of 11 | 0 | 9 (7 / 2): 8 closed, 1 disputed; max severity high, max open severity low | 2 / 1 | 2 (performance, monitoring) | 39 | 0 | 43 s |
| `default` | `claude-opus-5-5` | completed and sealed; signed off with `approve_with_conditions` (the sign-off policy refused `approve` on four open high findings) | 0 of 11 (complete) | 0 | 13 (2 / 11): 4 high, 7 medium, 2 low; 3 closed, 10 open at sign-off; all 22 responses accepted, none disputed | 3 / 2 | 8 | 110 (0 schema retries) | 7.93 for the pipeline (scope 0.04, document 1.93, challenge 0.79, remediate 5.16) plus 3.19 for the six challenger probes | about 26 min of node time (document 540 s, challenge 113 s, remediate 911 s); 49 min wall, including a 22 min park at a 6 USD cost cap in the second remediation round, resumed with `modelforge resume --cost-cap 10.5` |
| `ci-cheap` | `claude-sonnet-5-5` | not run in SP4 (not authorised beyond the `default` run); the `evals.yml` workflow needs a committed `evals/baselines/ci-cheap.json` before its comparison can pass | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| `default` (before the challenger) | `claude-opus-5-5` | completed; document rendered and sealed | 1 of 11 | 0 | n/a | n/a | n/a | 31 (4 sections regenerated once) | 2.03 | 10 min 30 s |
| `local-ollama` (before the challenger) | `gpt-oss:20b` | completed; document rendered with banners | 10 of 11 | 159 | n/a | n/a | n/a | 52 (9 schema retries) | 0 | 26 min 8 s |
| `local-lmstudio` (before the challenger) | `google/gemma-4-26b-a4b-qat` | parked in `needs_attention` at `document` (`provider`: output hit the 8192-token limit) | n/a | n/a | n/a | n/a | n/a | 6 | 0 | 3 min 28 s |
| `local-lmstudio` with `MODELFORGE_LMSTUDIO_MODEL=meta/muse-glimmer` (before the challenger) | `meta/muse-glimmer` | parked in `needs_attention` at `document` (`provider`: a planning call hit the 1024-token limit) | n/a | n/a | n/a | n/a | n/a | 9 | 0 | 33 min 5 s |

Under `fake` the seven pre-check findings are four undiscussed calibration, stability and benchmark tests (`cal.hl`, `cal.eo`, `stab.csi.max`, `bench.gap`) and three unreported passing out-of-time and sensitivity results (`disc.ks.oot`, `sens.signs`, `sens.monotonic`), all closed when the performance section was regenerated; the two review findings are the scripted monitoring finding, closed when the monitoring section was regenerated, and the scripted governance finding, which the scripted developer disputes. That dispute is low severity, so `approve` is allowed. Every number in the sealed document traces to an artifact. The `fake` findings are scripted, so this row shows the loop at work, not a review.

The `default` row's eval metrics, from the committed `default` baseline: retrieval recall 1.0, grounding pass rate 1.0, scope exact match, and defect recall 0.6 over the five applicable classes (`removed_section`, `claimed_absent_test` and `miscited_regulation` detected; `undiscussed_breach` undetected because the removed test was still named elsewhere in the document; `missing_limitation` undetected because the reviewer raised the proxy issue under methodology and conceptual soundness rather than under data with the limitations section as evidence), with defect precision 0.07 (baseline findings are matched by title, which a live model rewords, so its findings on a mutated document count as extra). What these numbers mean: the document was fully grounded, with every number tracing to an artifact. The Opus reviewer raised substantive findings that a rewrite cannot resolve: the horizon file used as an out-of-time proxy, pooled horizons, failed out-of-time calibration and 401 duplicate rows. The Opus developer accepted every finding instead of disputing it or accepting it as a documented limitation, so the second remediation round repeated the cost without converging, and the run was sealed with conditions. The precision metric needs matching by category and section rather than by title before it is meaningful for live models.

What the numbers mean for the earlier rows. The Anthropic run's one banner is the governance section, whose regenerated text still named SR 26-2 without a `[K:]` passage; every number in the document traces to an artifact. The Ollama model wrote fluent prose but did not follow the alias-marker rule: most of its "ungrounded numbers" are the digits of regulation names written in a form the checker's regulation-name pattern does not recognise, and its markers often misuse the path grammar, so ten sections carry the attention banner and the failure list. Gemma 4 on LM Studio never terminated its JSON for one writing call even with the larger budget, so the gateway recorded the provider error and the run parked; `resume` re-runs the node. These are measurements of the models under this prompt and checker, not of the data pipeline, which produced the same artifacts in every run.

## Compose

```bash
make up     # build the image and start the five services
make demo   # make up, then submit the demo run through the API
make down   # stop the stack
```

| Service | Image | Published port | Role |
| --- | --- | --- | --- |
| `postgres` | `pgvector/pgvector:pg16` | `${MODELFORGE_POSTGRES_PORT:-5432}` | lineage, checkpoints, queue and settings |
| `jaeger` | `jaegertracing/jaeger:2.23.0` | `16686` (UI) | trace collector and UI; OTLP/HTTP on 4318 inside the network |
| `phoenix` | `arizephoenix/phoenix:version-21.0.0` | `6006` | LLM trace UI; OTLP/HTTP on 6006 |
| `api` | built from the `Dockerfile` | `8000` | the API and the console; health check on `GET /api/v1/health` |
| `worker` | built from the `Dockerfile` | none | executes queued runs |

The image is built in two stages: a `node:22.23.4-alpine` stage runs `npm ci` and `npm run build` for the console, and the Python stage copies the result to `/app/console/dist` (`MODELFORGE_CONSOLE_DIR`) next to the templates, prompts, profiles, knowledge corpus and example briefs (`MODELFORGE_BRIEFS_DIR=/app/examples/briefs`). It runs `modelforge worker` by default; the `api` service runs `modelforge-api`. It defaults to the `fake` profile, so the stack runs offline; set `MODELFORGE_PROFILE` (and `ANTHROPIC_API_KEY` for `default` or `ci-cheap`) in the shell or `.env` before `make up` to use a real model. The API and the worker connect to the Compose Postgres, share the `./artifacts` and `./data` mounts, export traces to both collectors (`MODELFORGE_OTLP_ENDPOINTS`) and run with `MODELFORGE_AUTO_REPLAY=1`; the API links the console to `MODELFORGE_JAEGER_URL=http://localhost:16686` and `MODELFORGE_PHOENIX_URL=http://localhost:6006`. Phoenix 21 turns authentication on by default, so Compose sets `PHOENIX_ENABLE_AUTH=false` for this local collector. The worker executes runs submitted through the API or with `--detach`; `docker compose logs worker` shows its JSON logs, starting with a `worker.ready` line, and `docker compose logs api` the API's.

`MODELFORGE_POSTGRES_PORT` changes only the port Postgres is published on; a CLI or test on the host then needs `MODELFORGE_DATABASE_URL` pointing at that port. A second stack can run beside another with its own project name, for example `MODELFORGE_POSTGRES_PORT=5433 docker compose -p modelforge-demo up -d --build --wait`, as long as ports 8000, 16686 and 6006 are free.

## Configuration

The orchestrator reads these environment variables (a `.env` file fills any that are unset; an empty gateway variable counts as unset):

| Variable | Default |
| --- | --- |
| `MODELFORGE_DATABASE_URL` | `postgresql://modelforge:modelforge@localhost:5432/modelforge` |
| `MODELFORGE_ARTIFACT_DIR` | `./artifacts` |
| `MODELFORGE_DATA_DIR` | `./data` |
| `MODELFORGE_TEMPLATES_DIR` | `./templates` |
| `MODELFORGE_PROFILE` | `default` |
| `MODELFORGE_MCP_COMMAND` | unset (`python -m modelforge_mcp`) |
| `MODELFORGE_TOOL_TIMEOUT_S` | `600` |
| `MODELFORGE_LOG_LEVEL` | `INFO` |
| `MODELFORGE_PROMPTS_DIR` | `./prompts` |
| `MODELFORGE_PROFILES_DIR` | `./profiles` |
| `MODELFORGE_KNOWLEDGE_DIR` | `./knowledge-base` |
| `ANTHROPIC_API_KEY` | unset |
| `MODELFORGE_ANTHROPIC_BASE_URL` | unset (the gateway never reads `ANTHROPIC_BASE_URL`) |
| `MODELFORGE_OLLAMA_BASE_URL` | `http://localhost:11434` |
| `MODELFORGE_OLLAMA_MODEL` | the profile's model |
| `MODELFORGE_OLLAMA_NUM_CTX` | `32768` |
| `MODELFORGE_LMSTUDIO_BASE_URL` | `http://localhost:1234/v1` |
| `MODELFORGE_LMSTUDIO_MODEL` | the profile's model |
| `MODELFORGE_COST_CAP_USD` | `10` |
| `MODELFORGE_LLM_TIMEOUT_S` | `300` |
| `MODELFORGE_OTLP_ENDPOINTS` | unset (no tracing); a comma-separated list of OTLP/HTTP trace URLs |
| `MODELFORGE_TRACE_CONTENT` | `0` (`1` adds prompts and completions to the LLM spans; local collectors only, see "Tracing") |
| `MODELFORGE_AUTO_REPLAY` | `0` (the seed of the `auto_replay` setting; `1` in Compose) |

`MODELFORGE_PROFILE`, `MODELFORGE_COST_CAP_USD` and `MODELFORGE_AUTO_REPLAY` only seed the server-side settings for the API and the worker; once seeded, the database value wins (see "Server-side settings").

The API reads the same variables and these:

| Variable | Default |
| --- | --- |
| `MODELFORGE_API_HOST` | `0.0.0.0` |
| `MODELFORGE_API_PORT` | `8000` |
| `MODELFORGE_CONSOLE_DIR` | `console/dist` (`/app/console/dist` in the image) |
| `MODELFORGE_CONSOLE_DEV_URL` | unset; when set, `/` and every console path redirect there (development mode) |
| `MODELFORGE_BRIEFS_DIR` | `examples/briefs` (`/app/examples/briefs` in the image) |
| `MODELFORGE_USERS` | unset (every identity has every role); `name:role[,role]` entries separated by spaces, a duplicate name is an error |
| `MODELFORGE_JAEGER_URL` | `http://localhost:16686` (empty: no Jaeger link) |
| `MODELFORGE_PHOENIX_URL` | `http://localhost:6006` (empty: no Phoenix link) |

Compose also reads `MODELFORGE_POSTGRES_PORT` (default `5432`), the host port Postgres is published on.

The brief carries the run's own settings. Besides the model fields it takes an optional `restricted_terms` list (see "Redaction") and `remediation_rounds`, the bound on the remediation loop (default 2, from 0 to 3; 0 means the challenger reviews once and the run goes straight to sign-off). The evals read `evals/evals.yml` (see "Evals").

## Commands

```bash
make sync      # uv sync --all-groups and npm ci --prefix console
make lint       # ruff, ruff format, pyright, import-linter, the console's typecheck and lint
make test       # full Python suite, then the console tests
make test-fast  # Python only, skip tests marked slow
make example    # end-to-end example pipeline
make db-up      # start Postgres
make db-down    # stop the Compose stack
make migrate    # uv run modelforge migrate
make worker     # uv run modelforge worker
make goldens    # regenerate the golden artifacts
make evals      # eval harness under fake, compared with evals/baselines/fake.json
make evals-live PROFILE=default  # eval harness under a live profile against its baseline
make evals-baseline              # rewrite evals/baselines/fake.json
make up         # build the image and start the five Compose services
make down       # stop the Compose stack
make demo       # make up, then submit the demo run and print its console URL
make demo-down  # same as make down
make api        # uv run modelforge-api (serve the API and the built console on port 8000)
make console    # build the console into console/dist
make console-test      # the console tests
make console-types     # regenerate console/openapi.json and console/src/api/types.gen.ts
make console-fixtures  # regenerate console/src/fixtures (needs Postgres)
make ui-dev     # Vite dev server with /api proxied to localhost:8000
```

The `modelforge` CLI's commands are `migrate`, `run`, `approve`, `status`, `resume`, `pause`, `abort`, `replay`, `verify` and `worker` (see "Run a governed model build"); `status` adds a `findings:` line (count by status and the highest severity) once the challenger has run. The `modelforge-evals` CLI has `run` (a profile's layers, the report and the baseline comparison) and `compare` (a report file against a baseline file); see "Evals". The `modelforge-api` CLI has `serve` (the default), `openapi` (print the OpenAPI document) and `fixtures --out DIR` (write the console fixtures); see "API" and "Console".

Tests marked `db` need Postgres and skip when it is unreachable; set `MODELFORGE_REQUIRE_DB=1` to make them fail instead (CI does).

## Datasets

- Polish companies bankruptcy data, UCI Machine Learning Repository, DOI 10.24432/C5F600, licensed CC BY 4.0.
- Taiwanese bankruptcy prediction, UCI Machine Learning Repository, DOI 10.24432/C5004D, licensed CC BY 4.0.

Both are corporate bankruptcy datasets, not retail credit data, so two proxies are documented and applied:

- **Bankruptcy as default.** The bankruptcy label stands in for the default flag of a PD model.
- **Horizon files as vintages.** The Polish data ships as five files by forecasting horizon (`1year` to `5year`); each file is treated as a vintage, so a held-out file gives an out-of-time split. The Taiwanese set has no time dimension at all, so it uses a stratified random split and every out-of-time test is reported as not applicable.

## Status

Sub-projects 1 to 5 complete: the core toolkit; the governed orchestrator with its lineage store and MCP servers; the LLM gateway, knowledge layer, and scope and documentation agents; the independent challenger, the bounded remediation loop, the sign-off rule on blocking findings, and the eval harness; and the API, the console, tracing to Jaeger and Phoenix, pause and resume, manual and automatic replay, server-side settings, costs across runs and `make demo`. Sub-project 6 (the README overhaul, an architecture diagram, a design memo, a threat-model document, a domain primer and a demo video) has not started. See the architecture spec at `docs/superpowers/specs/2026-11-09-modelforge-architecture-design.md`, the orchestrator spec at `docs/superpowers/specs/2026-11-09-sp2-orchestrator-design.md`, the gateway and agents spec at `docs/superpowers/specs/2026-11-09-sp3-gateway-knowledge-agents-design.md`, the challenger, remediation and evals spec at `docs/superpowers/specs/2026-11-10-sp4-challenger-remediation-evals-design.md`, the API and console spec at `docs/superpowers/specs/2026-11-10-sp5-api-console-design.md`, and the implementation plans at `docs/superpowers/plans/2026-11-09-sp1-core-toolkit.md`, `docs/superpowers/plans/2026-11-09-sp2-orchestrator.md`, `docs/superpowers/plans/2026-11-09-sp3-gateway-knowledge-agents.md`, `docs/superpowers/plans/2026-11-10-sp4-challenger-remediation-evals.md` and `docs/superpowers/plans/2026-11-10-sp5-api-console.md`.
