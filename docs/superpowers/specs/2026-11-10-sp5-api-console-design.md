# SP5: API, Console, Tracing and the Demo

**Status:** approved in brainstorming (2026-11-10), awaiting written review
**Date:** 2026-11-10
**Parent:** `docs/superpowers/specs/2026-11-09-modelforge-architecture-design.md` sections 4.2, 4.3, 4.4, 5.4, 10.5, 11, 12, 13, 14.2, 15, 17 row 5 and sections 20 and 21 (SP3 and SP4 as built); `docs/superpowers/specs/2026-11-10-sp4-challenger-remediation-evals-design.md` section 16; `docs/superpowers/notes/2026-11-10-sp4-handoff.md` ("Recommendations for SP5", "Deferred minors", "Rulings") and the SP2 and SP3 notes' recommendations that name SP5. Brainstorm record: the shared notes folder, `sp5-brainstorm-2026-11-10.md`.
**Branch:** `sp5-api-console`, cut from `main` at `7f5bece`.

---

## 1. Purpose and done criteria

SP5 puts a control plane and a console over the governed run: a FastAPI service over lineage, a React console served from that service, OpenTelemetry traces across the API, the orchestrator nodes, the gateway and the MCP tool calls, and one command that brings the stack up with a demo run in it. It also adds what the user asked for beyond the umbrella's SP5 row: pause and resume of a running run, manual and automatic replay from the console, cost tracking across runs, server-side editable settings, and Arize Phoenix next to Jaeger.

Done when, at the branch head:

1. `make lint` is clean (ruff, ruff format, pyright, import-linter with the nine contracts of section 2, the console's typecheck and lint) and `MODELFORGE_REQUIRE_DB=1 make test` passes the full suite, slow tests included, the API tests against a temporary database and the console unit tests.
2. `make demo` builds the image, starts Postgres, Jaeger, Phoenix, the API and the worker, submits `examples/briefs/polish-pd.yaml` under `fake` through the API and prints `http://localhost:8000`. In the browser the run is listed, its detail page shows the step timeline and the scope gate within minutes, two identities approve the three gates (four-eyes and the sign-off rule enforced by lineage, the refusals shown), the document is readable with per-section grounding and citation hovers, the validation report lists the thirteen tests, findings and responses are shown per round, the event chain verifies, the cost and LLM-call summary is shown, every change arrives live over SSE without reloading, the sealed run is replayed automatically and the reproducibility badge appears, a manual replay with `--rerun-llm` can be started, a running run can be paused and resumed, the settings page edits the server-side defaults, and Jaeger shows one trace for the run with node, tool-call and LLM-call spans; Phoenix shows the same LLM spans.
3. The console is served from `/` by the API in the Docker image; `console/dist` is git-ignored and built in CI and in the image; the Vite dev server is documented as opt-in.
4. The README's run instructions are correct for the API, the console, Compose, Jaeger, Phoenix and the dev mode (the SP6 overhaul is not started).
5. `docs/superpowers/notes/2026-11-10-sp5-handoff.md` records verification, deliverables, SP6 recommendations, deferred minors and rulings in the SP4 note's shape; the umbrella spec gains section 22 (SP5 as-built amendments).

Out of scope (SP6 and later): the README overhaul, diagrams, the MRM memo, the threat-model document, the video; real authentication; a worker lease or heartbeat; non-root containers and digest-pinned images; cancellation of a running run (abort stays refused while `running`; pause first); scheduled replays; the LLM judge.

## 2. Packages and dependency rules

- New package `packages/api` (`modelforge_api`, console script `modelforge-api`), a workspace member added to the root `pyproject.toml` dependencies, `[tool.uv.sources]`, pytest `testpaths`, pyright `include` (already `packages`) and the Dockerfile's dependency layer. Dependencies: `modelforge-orchestrator` (and through it everything below), `fastapi`, `uvicorn[standard]`, `opentelemetry-instrumentation-fastapi`, `pyyaml`, `pydantic`.
- New console at `console/` (not the umbrella's `ui/`; `.gitignore` swaps `ui/dist/` for `console/dist/` and adds `console/node_modules/`).
- Import-linter: the layers contract becomes `(modelforge_api | modelforge_evals) > modelforge_orchestrator > (modelforge_mcp | modelforge_gateway) > modelforge_knowledge > modelforge_lineage > modelforge_core`; contracts 6 (no provider SDKs or HTTP clients) and 7 (only `modelforge_gateway.providers` imports the LangChain integrations) add `modelforge_api` to their source lists; contract 1 adds `opentelemetry` and `fastapi` to what `modelforge_core` may not import; a ninth contract, "api reaches models and tools only through the orchestrator and gateway", forbids `modelforge_api` from importing `langchain_core`, `langgraph`, `langchain_anthropic`, `langchain_ollama`, `langchain_openai` and `mcp` directly (`allow_indirect_imports = true`, as contracts 7 and 8). Nine contracts after SP5. The API never calls a model: it imports no provider, builds no `Gateway`, and the only gateway function it reaches is `check_credentials` through `submit.create_run_from_brief`, which reads configuration.
- Tracing dependencies (`opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`) are declared by `modelforge-lineage`, which owns `modelforge_lineage.tracing` next to `observability.py`. `opentelemetry-api` is already in the lock.
- Everything else as SP4: no pickle, explicit seeds, no wall-clock values inside artifacts, MCP servers untouched (no new tool, no version bump), prompts immutable, SR 26-2 wording guarded by the wording test (which now also scans `console/src` for the phrase "extends SR 11-7"), employer-neutral files, `modelforge_core` unmodified.
- Node: 22 LTS in the Docker build stage and in CI; the umbrella's "Node 20+" holds for developers. `make sync` runs `uv sync --all-groups` and `npm ci --prefix console`.

## 3. The API (`packages/api`, `modelforge_api`)

### 3.1 Application and settings

`create_app(settings: ApiSettings) -> FastAPI` builds the application; nothing connects to Postgres until the lifespan starts, so `modelforge-api openapi` can print the OpenAPI document without a database. `ApiSettings` wraps the orchestrator's `Settings` (`load_settings()`, so the database URL, directories, profile, prompts, profiles, knowledge and gateway settings are read the same way, `.env` included) and adds:

| Variable | Default | Meaning |
|---|---|---|
| `MODELFORGE_API_HOST`, `MODELFORGE_API_PORT` | `0.0.0.0`, `8000` | uvicorn bind |
| `MODELFORGE_CONSOLE_DIR` | `console/dist` | the built console |
| `MODELFORGE_CONSOLE_DEV_URL` | unset | when set, `/` and every non-API path redirect there (307) |
| `MODELFORGE_BRIEFS_DIR` | `examples/briefs` | the example briefs offered by `GET /briefs` |
| `MODELFORGE_USERS` | unset | the role map of section 3.2 |
| `MODELFORGE_JAEGER_URL`, `MODELFORGE_PHOENIX_URL` | `http://localhost:16686`, `http://localhost:6006` | the trace UIs the console links to (`null` in `/config` when empty) |
| `MODELFORGE_OTLP_ENDPOINTS`, `MODELFORGE_TRACE_CONTENT` | see section 5 | tracing |
| `MODELFORGE_AUTO_REPLAY` | `0` | the seed value of the `auto_replay` setting (section 3.8); `1` in Compose |

The `modelforge-api` command: `serve` (the default when no subcommand is given: `--host`, `--port`, `--reload` for development), `openapi` (prints the schema as JSON), `fixtures --out DIR` (section 6.4). The API's `Database` pool is sized `max_size=16`. Every API process calls `configure_logging` and `configure_tracing("modelforge-api")` at startup.

The CLI's checkpoint readers move to `modelforge_orchestrator/inspect.py`: `run_state(db, run_id) -> RunState | None` (the checkpointed state, `None` before the first step), `run_refs(db, run_id) -> dict[str, ArtifactRef]`, `gate_prompt(db, run_id) -> dict | None`, `run_snapshot(db, run_id) -> dict` (the `status --json` document). `cli.py` calls them; the API imports them and never a LangGraph type.

### 3.2 Identity and roles

Every write takes the acting identity from the `X-ModelForge-User` header: 1 to 64 characters of `[A-Za-z0-9._@-]`; a missing or malformed header is `401` with kind `identity`. Roles come from `MODELFORGE_USERS`, a space-separated list of `name:role[,role]` entries, roles `requester` (may submit a run), `approver` (may decide a gate, pause, resume, abort and replay) and `admin` (may change settings). Unset means every identity has every role (the demo, the tests). A write by an identity lacking the role is `403` with kind `role`. The four-eyes rule and the sign-off rule stay in `record_approval`; the API does not duplicate them. `GET /config` reports `identity: {mode: "open" | "roles", roles: {name: [roles]}}`. Reads need no identity. This is documented as the pluggable identity of umbrella 10.5; the CLI keeps `--approver`.

### 3.3 Read endpoints (prefix `/api/v1`)

Every response is a Pydantic model (so the OpenAPI document types the console). Timestamps are ISO 8601 strings. `404` kind `not_found` for an unknown run, artifact or model id; `409` kind `not_ready` when a run has no document, validation report or manifest yet.

| Method and path | Returns |
|---|---|
| `GET /health` | `{status: "ok" | "degraded", database: "ok" | <error>, version}`; `503` when the database query fails |
| `GET /config` | the redacted effective configuration: profile default, console mode (`built`, `missing`, `dev`), identity (3.2), trace UI URLs, tracing endpoints and whether content tracing is on, directories, versions of core, orchestrator and api, `nodes` (the graph's node names in order, from `modelforge_orchestrator.graph.NODES`) and `gates` (the three gate names with their statuses and valid decisions, from lineage's `VALID_DECISIONS`); never a URL with a password or a key |
| `GET /briefs` | `[{name, path, text}]` for `MODELFORGE_BRIEFS_DIR/*.yaml`, sorted by name |
| `GET /runs?limit&offset&status&profile&requester&replays` | `{runs: [RunListItem], total, limit, offset}`, newest first; `RunListItem` is the `RunRecord` plus `model_name` (the inventory entry's name, `null` before intake) and `findings: {n, max_open_severity}`; `replays=exclude` hides replay runs (default `include`); `limit` 1 to 200, default 50 |
| `GET /runs/{id}` | `RunDetail`: `run` (`RunRecord`, now with `pause_requested` and `trace_id`), `refs` (name to `ArtifactRef`), `counters` and `max_remediation_rounds` from the checkpoint (`null` before the first step), `gate` (`{gate, summary}` from the latest `gate.requested` event, only while the run awaits it, else `null`), `approvals`, `findings_summary` (the `findings` ref summary or `null`), `responses_summary`, `cost` (3.3.1), `inventory` (the entry or `null`), `trace: {trace_id, jaeger_url, phoenix_url}` (URLs `null` when unconfigured), `replays: [{run_id, status, rerun_llm, report: ReplayReport | null}]` |
| `GET /runs/{id}/steps` | `[{...StepRecord, duration_ms, tool_calls: [ToolCallRecord], llm_calls: [LlmCallRecord]}]` in step order |
| `GET /runs/{id}/llm-calls` | `{calls: [LlmCallRecord], cost: <3.3.1>}` |
| `GET /runs/{id}/artifacts` | `[{...ArtifactRow, ref_names: [str], summary}]` (the ref names that point at the artifact, from the checkpoint; the summary from the ref) |
| `GET /runs/{id}/artifacts/{artifact_id}` | the content: JSON (`application/json`) for `application/json` artifacts, text for YAML, Markdown and HTML artifacts (their own media type), and a download (`Content-Disposition: attachment`) for everything else; only artifacts that belong to the run |
| `GET /runs/{id}/document?format=sections|html|md` | `sections` (default): `{template_id, document_id, sections: [{section_id, title, required, body_md, grounding, citations: [ResolvedCitation]}]}` from the run's current `document_sections` ref, titles from the template; `html` and `md`: the stored artifacts with their media type |
| `GET /runs/{id}/validation` | the `ValidationReport` JSON (`model_name`, `thresholds`, `tests[13]`, `overall`) |
| `GET /runs/{id}/findings` | `{findings: [FindingRow], responses: [FindingResponse], summary: {n_findings, by_severity, by_status, by_source, max_severity, max_open_severity, challenge_rounds, remediation_rounds, sections_regenerated: [str]}}`; rows carry their short id (`F-1`), the row id and `citations` (the markers of `evidence` and `response_evidence` resolved as in 3.3.2, one per distinct marker; section ids are not markers); responses come from the `responses` ref (empty when none) |
| `GET /runs/{id}/approvals` | `[ApprovalRecord]` |
| `GET /runs/{id}/manifest` | the sealed `Manifest` JSON |
| `GET /runs/{id}/verify` | the `VerifyReport` from `verify_run` |
| `GET /runs/{id}/events?after&limit` | `{events: [EventRecord], next_after}`; `after` is an event id (default 0), `limit` 1 to 1000 (default 500) |
| `GET /runs/{id}/replays` | `[{run: RunRecord, report: ReplayReport | null}]` (runs whose `replay_of` is the id, oldest first) |
| `GET /inventory`, `GET /inventory/{model_id}` | `[InventoryEntry]` newest first; one entry |
| `GET /costs?since&until` | section 3.9 |
| `GET /settings` | section 3.8 |

**3.3.1 Cost summary.** `{spent_usd, cap_usd, n_calls, n_schema_retries, by_purpose: {purpose: {n_calls, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd}}, by_model: {model: {...same}}}` computed from the run's `llm_calls` rows (`n_schema_retries` is the number of rows with `schema_valid = false`).

**3.3.2 Resolved citations.** `ResolvedCitation = {marker, kind: "A" | "K", target, resolved: bool, artifact_id?, artifact_kind?, path?, value?, passage?: {citation_id, doc_id, title, section, text, url}, note?}`. An `[A:<artifact_id>#<path>]` marker resolves through `modelforge_lineage.citations.resolve_path` over the stored artifact's JSON (the same grammar the grounding check uses); a non-JSON artifact or an unresolvable path gives `resolved: false` with a `note`. A `[K:<citation_id>]` marker resolves through the run's `knowledge_index` artifact (`modelforge_knowledge`'s index model); an unknown id gives `resolved: false`. Values are returned as JSON (a number, a string, a list or an object), and a list or object longer than 2000 characters of JSON is truncated with `note: "truncated"`. Markers are found with the doc-renderer's `MARKER` pattern over `body_md`; the console does the same on the client to place the hovers.

### 3.4 Write endpoints

All take the identity header (3.2). None executes the graph: they write lineage rows and enqueue, and the worker executes, exactly as the CLI's `--detach`.

| Method and path | Body | Effect |
|---|---|---|
| `POST /runs` | JSON `{brief: <yaml text>, profile?, cost_cap_usd?}`, or the brief as the raw body with `Content-Type: application/yaml`, `application/x-yaml` or `text/yaml` and `profile` and `cost_cap_usd` as query parameters | `submit.create_run_from_brief` with the identity as requester, `profile` defaulting to the `default_profile` setting and the cap to `default_cost_cap_usd` (3.8); `201` with the `RunRecord`; `400` kind `brief` (parse or plan error, unknown adapter), `400` kind `profile` (unknown profile or missing credential, the gateway's config error message) |
| `POST /runs/{id}/approvals` | `{gate, decision, comment?, plan?, adjust_target?, conditions?}` (the `GateDecision` fields without `approver`) | `record_approval(..., loop_bound=3)` then `enqueue(resume)` in one transaction; `202` with the `ApprovalRecord`; `409` kind `approval` with the lineage message for `GateMismatchError`, `FourEyesError`, `LoopBoundError`, `InvalidDecisionError` and `BlockingFindingsError` (the last also carries `findings: [{id, severity, title}]`); `422` for a plan that does not validate |
| `POST /runs/{id}/resume` | `{cost_cap_usd?}` | from `needs_attention` or `paused`: optionally `set_cost_cap`, clear the error, `enqueue(resume)`; `202` with the run; `409` kind `state` otherwise |
| `POST /runs/{id}/pause` | none | from `running`: set `pause_requested` (section 4.2); `202` with the run (the status changes at the next node boundary); `409` kind `state` otherwise |
| `POST /runs/{id}/abort` | none | as the CLI: refused while `running` (`409`, "pause first") or final; from `queued` the queue row is released; `200` with the run |
| `POST /runs/{id}/replay` | `{rerun_llm?: bool}` | `create_replay` then the queue row it writes; `202` with the replay `RunRecord`; `409` kind `state` for an unsealed run (the `ReplayError` message) |
| `PATCH /settings` | section 3.8 | |

### 3.5 Server-sent events

`GET /runs/{id}/events/stream` and `GET /events/stream` answer `text/event-stream` (`Cache-Control: no-cache`, `X-Accel-Buffering: no`). Each message is `id: <event row id>`, `event: <event type>` and `data: <EventRecord JSON>` (the global stream's records carry `run_id` as every record does). History replays from the `Last-Event-ID` request header, or `?after=<id>`, else from the beginning for the run stream and from the current head for the global stream (so a new run-list subscriber does not receive every event ever written); an id above the run's newest event (left from before a database reset) starts the run stream from the beginning, and one above the head starts the global stream at the head. The handler is an async generator that reads new rows with `asyncio.to_thread` every 0.5 s (`id > last`, ordered by id, in pages of 500), writes a `: keepalive` comment every 15 s, and ends when the client disconnects (`request.is_disconnected()`); no connection or thread is held between polls. No `LISTEN`/`NOTIFY`. The run stream `404`s an unknown run before streaming.

### 3.6 Console serving

`/api/v1/*` and `/openapi.json`, `/docs` are the API. Everything else is the console: `MODELFORGE_CONSOLE_DIR/assets` is mounted as static files with long cache headers (Vite hashes the names), `index.html` is returned for every other path with `Cache-Control: no-cache` (client-side routing), and the request's `Accept` is not consulted. When `MODELFORGE_CONSOLE_DEV_URL` is set, `/` and those paths redirect (307) there. When the directory has no `index.html`, `/` returns a short HTML page (200) saying the console is not built and giving `make console` and `make ui-dev`, and `/config` reports `console: "missing"`.

### 3.7 Errors

One error shape for every non-2xx response the API writes itself: `{"error": {"kind": <str>, "message": <str>, ...extra}}`; FastAPI's validation errors (422) are mapped into it (`kind: "validation"`, `detail` carried as extra). Kinds: `identity`, `role`, `not_found`, `not_ready`, `brief`, `profile`, `approval`, `state`, `validation`, `settings`, `internal`. An unexpected exception is `500` kind `internal` with the exception type and no traceback, and is logged.

### 3.8 Server-side settings

Migration `0005_sp5.sql` adds `settings (key text primary key, value jsonb not null, updated_at timestamptz not null default now(), updated_by text null)` and `settings_audit (id bigserial primary key, key text not null, old_value jsonb null, new_value jsonb not null, updated_by text not null, ts timestamptz not null default now())`. Repository `modelforge_lineage/settings.py`: `get_all(conn) -> dict[str, Any]`, `get(conn, key, default)`, `set_value(conn, key, value, *, updated_by)` (writes the audit row in the same transaction), `list_audit(conn, limit)`.

Editable keys and their seeds: `default_profile` (seeded from `MODELFORGE_PROFILE`; must name a file in the profiles directory), `default_cost_cap_usd` (seeded from `MODELFORGE_COST_CAP_USD`, the gateway default 10; positive), `auto_replay` (seeded from `MODELFORGE_AUTO_REPLAY`; boolean). `settings.seed(conn, defaults)` writes a key only when it is absent; the CLI's `migrate` command, the worker at start (after `migrate.apply`) and the API in its lifespan call it with the seed values from their settings. After seeding the database value wins over the environment for the API and the worker. The CLI `run` command keeps its environment defaults (unchanged).

`GET /settings` returns `{editable: {default_profile, default_cost_cap_usd, auto_replay}, audit: [last 20 audit rows], config: <the `GET /config` document plus profiles: [{name, routes: {purpose: {provider, model, effort, structured}}, prices}]>}`. `PATCH /settings` takes any subset of the editable keys, needs the `admin` role, validates (`400` kind `settings` for an unknown profile or a non-positive cap, `422` for a wrong type), writes each changed key with its audit row, and returns the new `GET /settings` document. Identity and tracing stay environment-only (they are security and process configuration).

### 3.9 Costs across runs

`GET /costs?since&until` (ISO timestamps, default the last 30 days to now, bounded by `llm_calls.ts`) returns `{since, until, total_usd, n_runs, n_calls, tokens: {input, output, cache_read, cache_write}, by_run: [{run_id, status, profile, requester, created_at, cost_usd, cost_cap_usd, n_calls}], by_profile: [{profile, cost_usd, n_runs, n_calls}], by_model: [{model, cost_usd, n_calls, input_tokens, output_tokens}], by_purpose: [{purpose, cost_usd, n_calls}], by_day: [{day, cost_usd, n_calls}]}`, aggregated in SQL over `llm_calls` joined to `steps` and `runs`; `by_run` lists every run created in the window, with zero cost when it made no call. `fake` runs cost 0 and still count.

### 3.10 Tests (`packages/api/tests`)

FastAPI's `TestClient` over `create_app` with a temporary database (the lineage fixtures) and a temporary artifact directory. The end-to-end fixture submits the synthetic brief through `POST /runs` under `fake` and drives it with `worker.run_once` (in-process, `StdioToolClient`, as the eval harness does) between API calls. Covered: submit (JSON and YAML bodies, defaults from settings, the four error kinds); every read endpoint's shape on the completed run, including the resolved document citations (an `[A:]` value equal to the artifact's and a `[K:]` passage from the index), the validation report's thirteen test ids, findings grouped with responses, replays, steps with tool and LLM calls, costs, inventory; approvals through all three gates with a four-eyes refusal (`409` naming the requester) and a blocking-findings refusal (constructed with a planted open high finding row); resume from a cost-cap park (a run created with a tiny cap parks at `scope`, `POST resume` with a higher cap, `run_once` continues); pause: a pause requested before `run_once` parks the run `paused` at the next node with the `run.paused` event, resume continues to the gate; abort refusals; replay (plain and `rerun_llm`) and the replay list with its report; settings read, seed values, `PATCH` with audit rows, role refusal under a role map; identity `401` and `403`; SSE replay from `Last-Event-ID` and live delivery of an event appended during the stream (read with a streaming client); SPA fallback, the missing-console page and the dev redirect; `GET /health` and `/config` redaction (no password in any string). Unit tests without a database: error mapping, identity parsing, the role map parser, SSE framing, citation resolution over an in-memory artifact.

## 4. Lineage and orchestrator changes

### 4.1 Migration `0005_sp5.sql`

`alter table runs add column pause_requested boolean not null default false; create index runs_replay_of_idx on runs (replay_of);` plus the two settings tables of 3.8. `RunRecord` gains `pause_requested: bool`. `RunStatus` gains `paused`.

### 4.2 Pause and resume

`modelforge_lineage.runs`: `request_pause(conn, run_id)`, `clear_pause(conn, run_id)`, `pause_requested(conn, run_id) -> bool`. `modelforge_orchestrator.steps`: `PauseRequested(Exception)` carrying `run_id` and `node`; `Step.__enter__` reads the flag before `start_step` and raises it when set, except for the nodes in `PAUSE_EXEMPT = {"gate_scope", "gate_model", "gate_signoff", "seal"}` (gates resume an interrupt and must not fail between the answer and their step; seal is seconds long). Nothing is written to lineage before the raise, so the checkpoint stays at the previous node's completion. `worker.execute` catches `PauseRequested` before the generic handler (unwrapping an `ExceptionGroup` if LangGraph wraps it): `set_status("paused")`, `clear_pause`, `append_event("run.paused", {node})`, release the queue row; the error column stays `null`. `_graph_input` returns `None` for a `resume` claim of a `paused` run, as for `needs_attention`, so LangGraph re-runs the pending node. In every other outcome of `execute` (park, finish, failure) the flag is cleared in the `finally`, so a request that arrived too late never leaks into a later execution. CLI: `modelforge pause <run_id>` (sets the flag; refused unless `running`), `resume` accepts `paused`, `abort` accepts `paused`. The evals harness treats `paused` like any non-final status (it never requests one).

### 4.3 Automatic replay on seal

After `execute` finishes a non-replay run with status `completed` and the `auto_replay` setting is true, the worker creates a plain replay (`create_replay(conn, run_id, rerun_llm=False)`) with the event payload `{"auto": true}` on the replay's `run.created` event, and the queue takes it from there. A failure to create the replay is logged and does not touch the original run. `create_replay` and `create_run_from_brief` both set `runs.trace_id` (section 5.5).

### 4.4 Inspect module

`modelforge_orchestrator/inspect.py` as 3.1: `run_state` reads the checkpoint through `build_graph(make_checkpointer(db)).get_state(...)` and validates `RunState`; `run_refs` is its `refs`; `run_snapshot` returns the `status --json` document unchanged; `cli.py` loses `_refs` and `_gate_prompt`.

## 5. Tracing (`modelforge_lineage.tracing`)

### 5.1 Configuration

`configure_tracing(service_name: str, endpoints: Sequence[str]) -> None` installs a `TracerProvider` with the resource `{service.name, service.version, openinference.project.name: "modelforge"}` and one `BatchSpanProcessor` with an OTLP/HTTP `OTLPSpanExporter` per endpoint; with no endpoint it installs nothing and the API's no-op tracer stays (tests, CI and the CLI without the variable are silent). `MODELFORGE_OTLP_ENDPOINTS` is a comma-separated list of OTLP/HTTP trace URLs (Compose: `http://jaeger:4318/v1/traces,http://phoenix:6006/v1/traces`); `MODELFORGE_TRACE_CONTENT` (`0`) is section 5.4. Both are read by `gateway_settings_from_env` into `GatewaySettings` (`otlp_endpoints: tuple[str, ...]`, `trace_content: bool`), so the worker, the CLI and the API share one source. `shutdown_tracing()` flushes; the worker calls it when `run_loop` ends, the CLI at the end of each command, the API in its lifespan. `tracer()` returns the project's tracer.

### 5.2 Spans and attributes

Attribute prefix `modelforge.`. Never a prompt, a message, an excerpt, a brief, a passage, an artifact value or a dataset value (5.4 is the one exception).

| Span | Where | Attributes |
|---|---|---|
| `run.execute` | `worker.execute`, around the graph invocation, under the run's trace (5.5) | `run_id`, `queue_kind`, `worker_id`, `profile`, `replay_of`, `prior_status`, `final_status`; `openinference.span.kind = "AGENT"` |
| `node.<name>` | `Step.__enter__` to `__exit__` | `run_id`, `step_id`, `node`, `attempt`, `status`, `input_state_hash`, `output_state_hash`; `openinference.span.kind = "CHAIN"`; the exception recorded with `record_exception` on failure (type and message only) |
| `tool.<server>.<tool>` | `BaseToolClient.call`, one span per attempt | `run_id` and `step_id` when the client knows them (the `Step` sets them through a context variable), `server`, `tool`, `version`, `attempt`, `duration_ms`, `input_hash`, `output_hash`, `artifact_ids`, `error_code` on failure; `openinference.span.kind = "TOOL"`, `tool.name` |
| `llm.<purpose>` | `Gateway._complete`, one span per attempt around `_invoke` | `run_id`, `step_id`, `section_id`, `purpose`, `provider`, `model`, `effort`, `prompt_name`, `prompt_version`, `prompt_hash`, `request_hash`, `attempt`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `cost_usd`, `latency_ms`, `stop_reason`, `schema_valid`, `error_kind` on a `GatewayError`; OpenInference `openinference.span.kind = "LLM"`, `llm.model_name`, `llm.provider`, `llm.token_count.prompt`, `llm.token_count.completion`, `llm.token_count.total`, `llm.invocation_parameters` (JSON of effort, max output tokens, structured method) |
| HTTP requests | the FastAPI instrumentor on the API app | the instrumentor's attributes; the run routes add `modelforge.run_id` to the current span; `/health` and the SSE streams are excluded (`excluded_urls`) |

### 5.3 Phoenix

Phoenix receives the same spans as Jaeger through its OTLP/HTTP endpoint and groups them under the project `modelforge`. The OpenInference attributes above are the ones Phoenix reads to show LLM spans with model and token counts; without content (the default) Phoenix shows the call structure, latency, tokens and cost, which is the intended default. The attribute names are string constants in `tracing.py` (no `openinference-semantic-conventions` dependency).

### 5.4 Content tracing (opt-in)

`MODELFORGE_TRACE_CONTENT=1` makes the gateway add to each `llm.*` span `llm.input_messages.<i>.message.role` and `.content` for the system and user messages as sent (after the redaction scan passed), `llm.output_messages.0.message.content` with the raw completion text, and `input.value` / `output.value` (the same as JSON). Default off. The README and the `/config` document state the threat-model exception: with content on, prompts and completions (which carry artifact excerpts, passages and the brief) leave the process for the configured trace collectors, so it is for local collectors only; the gateway logs one warning at startup when it is on.

### 5.5 One trace per run

`runs.trace_id` is set at creation (`create_run_from_brief` and `create_replay`, a random 128-bit id as 32 lowercase hex characters) and never changes. `worker.execute` starts `run.execute` under a parent context built from that id (`SpanContext(trace_id, span_id=random, is_remote=True, trace_flags=SAMPLED)` wrapped in a non-recording span), so the executions between gates, the resumes and the pauses of one run are one trace in Jaeger and one trace in Phoenix. Every node, tool and LLM span is a descendant of the current `run.execute` span through the normal context. LangGraph runs node functions in its own executor; the implementer verifies with a throwaway graph that the OpenTelemetry context (a contextvar) reaches the node, and if it does not, the worker puts the `run.execute` span's context on `Deps` (`trace_context`) and `Step` starts its span under it explicitly; the gateway and the tool client always use the current context, which `Step`'s span sets. The console links `${jaeger_url}/trace/${trace_id}` and `${phoenix_url}` (Phoenix's trace URLs need its internal ids; the link opens the project).

## 6. The console (`console/`)

### 6.1 Stack and structure

React 19, Vite, TypeScript (`strict`), TanStack Query, React Router, Tailwind CSS v4, `react-markdown` with `remark-gfm` (HTML disabled; a rehype step is not used) and a marker transform that turns `[A:...]` and `[K:...]` in the Markdown text into `<Citation>` hover elements, Vitest with React Testing Library and `@testing-library/user-event`, ESLint (the Vite React template's configuration). No component library (dark operations console, 6.6). Source layout: `src/api/` (`client.ts` with the identity header, `types.gen.ts` generated from OpenAPI, `queries.ts` with the TanStack hooks and the query keys), `src/events/` (`useRunEvents`, `useGlobalEvents`), `src/pages/`, `src/components/`, `src/fixtures/` (6.4), `src/test/`. Scripts: `dev` (Vite with `/api` proxied to `http://localhost:8000`), `build`, `preview`, `test` (`vitest run`), `typecheck` (`tsc --noEmit`), `lint` (ESLint), `types` (regenerates `types.gen.ts` from a schema file, 6.4).

### 6.2 Views

- **Runs** (`/`): the run table (id, model name, status pill, profile, requester, created, cost against cap, findings count and `max_open_severity`, replay badge), filters for status, profile and requester, a toggle to hide replays, live through the global stream; a "New run" button.
- **New run** (`/runs/new`): an example picker (from `GET /briefs`) that fills a YAML editor (a textarea with monospace), profile select (from `/settings` config, defaulting to the server default), cost cap (defaulting to the server default), submit; the server's `400` messages shown inline.
- **Run detail** (`/runs/:id`), a header with the id, status pill, model name, profile, requester, cost against cap, the trace links, and the actions the status allows (pause while `running` with "pause requested" shown once asked; resume with an optional new cap while `needs_attention` or `paused`; abort when allowed; replay, plain or with `--rerun-llm`, once sealed), and tabs:
  - **Overview**: the fourteen-node timeline in graph order (`intake` to `seal`) with each node's attempts, status, duration and counts of tool and LLM calls; the gate panel while the run awaits a gate, showing the gate summary (scope: the plan as an editable form with a diff against the proposal for `edit`; model: the validation table excerpt, selected features and build loops, `adjust` with target and plan; sign-off: findings by status and severity, responses, `max_open_severity`, conditions for `approve_with_conditions`) and the decision form, with the server's `409` message shown inline (four-eyes, blocking findings with their list); the `needs_attention` panel with the error record and the resume and abort actions; the approvals list; the replays panel with the reproducibility badge (a completed plain replay with `ok: true`: "reproduced, N artifacts matched"; mismatches listed; "replay queued/running" while one is in flight).
  - **Document**: a section navigation, each section's Markdown with a grounding badge (ok, or the lists of ungrounded numbers, unresolved markers, uncited regulatory sentences and foreign passages) and citation hovers (an `[A:]` shows the artifact kind, path and value; a `[K:]` shows the passage's document, section and text with a link to its source URL); the banner sections marked; download links for the stored HTML and Markdown.
  - **Validation**: the thirteen tests as a table (id, name, status pill, value, threshold, message) and the overall band; the thresholds listed.
  - **Findings**: grouped by the round that raised them, each with id, severity, source, category, status, title, description, evidence (markers rendered as hovers, section ids as links into the document), required action, the developer's response and evidence, the challenger's verdict and the round that closed it; a summary bar (counts by status and severity, `max_open_severity`, rounds) and the sections regenerated per remediation round from the responses; filters by status and severity.
  - **Lineage**: the artifacts table (kind, ref names, media type, size, id with copy, download or view) and the event chain (id, time, type, step, payload summary, `prev_hash` and `hash` prefixes) with a "verify" button that calls `GET /verify` and shows the report; the manifest when sealed.
  - **LLM calls**: the rows (step, purpose, provider, model, prompt, tokens, cost, latency, stop reason, schema valid) and the cost summary by purpose and model.
- **Inventory** (`/inventory`): the entries (model id, name, version, owner, tier, status, approver, conditions, run link).
- **Costs** (`/costs`): a window picker (7, 30, 90 days), tiles for total spend, runs, calls and tokens, bars by day (plain divs, no chart library), tables by run (cost against cap), by profile, by model and by purpose.
- **Settings** (`/settings`): the identity picker (name stored in `localStorage`; the role map shown when configured), the editable defaults (profile, cost cap, auto replay) with save (needs `admin` when roles are on; the `403` shown), the audit list, and the read-only configuration (profiles with routes and prices, directories, tracing endpoints and content flag with the warning, trace UI links, versions, console mode).

### 6.3 Live updates

`useRunEvents(runId)` opens an `EventSource` on the run stream with `Last-Event-ID` resumption (the browser's own); every event invalidates the run's query keys (prefix `['runs', id]`), debounced to 300 ms, and the latest 200 events are kept in memory for the Overview's live feed. `useGlobalEvents()` on the Runs page invalidates the run list on `run.created`, `run.status_changed`, `gate.requested` and `run.sealed`. A connection indicator in the header shows `live`, `reconnecting` or `offline`. Polling fallback: none; `EventSource` reconnects by itself.

### 6.4 Types and fixtures

`console/src/api/types.gen.ts` is generated by `openapi-typescript` from `console/openapi.json`, which `make console-types` writes with `modelforge-api openapi`; both files are committed and CI fails when `make console-types` changes them (`git diff --exit-code`). `console/src/fixtures/*.json` are the responses of the read endpoints for one completed synthetic `fake` run, written by `modelforge-api fixtures --out console/src/fixtures` (`make console-fixtures`, needs Postgres: it drives the run on a temporary database like the API tests); committed and refreshed by hand when an endpoint's shape changes. The fixtures are the console tests' data and the typed contract between the two halves.

### 6.5 Tests

Vitest with React Testing Library over the fixtures and a stubbed `fetch` (`src/test/server.ts`, a small router over the fixtures; no MSW): the run list renders rows, status pills and filters; the New run form fills from an example and posts the body with the identity header; the gate panel renders each gate's summary, posts the decision, and shows a `409` message; the timeline shows fourteen nodes with their statuses; the document renders sections with grounding badges and turns markers into hovers showing the resolved value or passage; the validation table shows thirteen rows; findings group by round with responses and verdicts; the lineage view shows the chain and the verify report; costs and settings render and the settings form patches; `useRunEvents` invalidates queries on a stubbed `EventSource` message and shows the connection state. CI runs `typecheck`, `lint`, `test` and `build`.

### 6.6 Visual direction: dark operations console

Dark by default and only (no theme switch): a graphite background (`#0f1115` range) with raised panels one step lighter, high-contrast off-white text, one accent (amber) for primary actions, focus and the live indicator, and a fixed status palette (queued grey, running blue, awaiting gates amber, needs_attention red, paused violet, completed green, rejected and aborted muted red). Severity colours: critical red, high orange, medium amber, low grey; statuses as filled pills, severities as outlined pills. Typography: a system sans for text and a monospace face for ids, hashes, numbers and the YAML editor, tabular numerals in tables. Layout: a narrow fixed left navigation (Runs, Inventory, Costs, Settings), a top bar with the identity and the connection indicator, dense tables with 32 px rows, panels with 1 px borders and no shadows, no illustration or decoration. Tokens live in one CSS file (`src/theme.css`) as custom properties that Tailwind's theme reads. The gates and the document viewer get the design effort (umbrella 13); the rest is plain and consistent.

## 7. Compose, image, Make, CI, `make demo`

- **Compose** services: `postgres` (unchanged except the published port, `${MODELFORGE_POSTGRES_PORT:-5432}:5432`), `jaeger` (the current Jaeger 2 image at a pinned release tag; UI `16686:16686`; OTLP HTTP on 4318 inside the network), `phoenix` (`arizephoenix/phoenix` at a pinned release tag; `6006:6006`; OTLP HTTP on 6006 inside the network), `api` (`build: .`, `command: ["modelforge-api"]`, `8000:8000`, `depends_on` postgres healthy, healthcheck `GET /api/v1/health` with `python -c` or `curl` as the image allows, environment `MODELFORGE_DATABASE_URL`, `MODELFORGE_OTLP_ENDPOINTS`, `MODELFORGE_JAEGER_URL=http://localhost:16686`, `MODELFORGE_PHOENIX_URL=http://localhost:6006`, `MODELFORGE_PROFILE`, `MODELFORGE_AUTO_REPLAY=1`, `ANTHROPIC_API_KEY`, the artifact and data mounts), `worker` (as today plus `MODELFORGE_OTLP_ENDPOINTS` and `MODELFORGE_AUTO_REPLAY=1`). The API and the worker share the `./artifacts` and `./data` mounts.
- **Dockerfile**: a `console` stage (`node:22-alpine`: copy `console/package.json` and `package-lock.json`, `npm ci`, copy `console/`, `npm run build`); the Python stage copies `packages/api/pyproject.toml` into the dependency layer, copies `console/dist` from the console stage to `/app/console/dist`, sets `MODELFORGE_CONSOLE_DIR=/app/console/dist` and `MODELFORGE_BRIEFS_DIR=/app/examples/briefs`, and changes `ENTRYPOINT ["uv", "run", "--no-sync"]` with `CMD ["modelforge", "worker"]`.
- **Make**: `demo` (below), `up` and `down` (the five services), `api` (`uv run modelforge-api`), `console` (`npm run build --prefix console`), `console-test`, `console-types`, `console-fixtures`, `ui-dev` (`npm run dev --prefix console`), `sync` (uv and npm), `lint` (adds `npm run typecheck --prefix console && npm run lint --prefix console`), `test` (adds `npm test --prefix console`), `test-fast` (Python only, not slow).
- **`make demo`**: `docker compose up -d --build --wait`, then wait for `GET /api/v1/health` to answer `ok` (up to 60 s), then `curl -sf -X POST http://localhost:8000/api/v1/runs?profile=fake -H 'X-ModelForge-User: alice' -H 'Content-Type: application/yaml' --data-binary @examples/briefs/polish-pd.yaml`, print the run id and `http://localhost:8000/runs/<id>`, and open the URL with `open` or `xdg-open` when one exists (best effort, never a failure). The worker downloads the dataset into the mounted `./data` on first use. `make demo-down` is `make down`.
- **CI** (`ci.yml`): the `lint-test` job gains `actions/setup-node@v4` (Node 22, npm cache on `console/package-lock.json`), `npm ci --prefix console`, and `make lint` and `make test` now cover the console; a `console-types` check (`make console-types && git diff --exit-code console/openapi.json console/src/api/types.gen.ts`); the `docker-build` job is unchanged and now builds the console stage.

## 8. Docs

- README: "Run a governed model build" gains the API and console path (`make demo`, the console URL, identity header, roles), a "Console" section (views, identity picker, dev mode with `make ui-dev` and `MODELFORGE_CONSOLE_DEV_URL`, building, fixtures and types), an "API" section (the endpoint table of 3.3 and 3.4 in brief, SSE, the error shape, the OpenAPI document at `/docs`), a "Tracing" section (Jaeger, Phoenix, one trace per run, the attributes, content tracing and its exception), pause, replay from the console and auto replay, settings and costs; the Compose and Configuration sections updated; the Commands list updated; the Status section names SP5. The SP6 overhaul (architecture diagram, memo, threat-model document, primer, video) is not started.
- `docs/superpowers/notes/2026-11-10-sp5-handoff.md` in the SP4 shape.
- Umbrella section 22: console at `console/`; the API's endpoint set as built (3.3 and 3.4 beyond section 12's table: steps, llm-calls, validation, verify, replays, pause, costs, settings, briefs, the SSE pair); SSE by polling; identity header and roles; `paused` status and `pause_requested`; auto replay; tracing in `modelforge_lineage.tracing`, OTLP/HTTP, Phoenix and content tracing; the settings tables; nine contracts and the `(api | evals)` layer.

## 9. Testing summary

- Lineage: migration 0005 applies; settings repository (seed only when absent, audit rows, `get` default); pause flag functions; `RunRecord` round trip with `pause_requested`; tracing configuration (no provider without endpoints; a provider with an in-memory exporter when a test passes a fake endpoint, span attributes checked without content; content attributes present only with the flag).
- Gateway: the `llm.*` span per attempt carries the attributes of 5.2 and, with `trace_content`, the messages; never otherwise.
- Orchestrator: `inspect` functions equal the CLI's former output; `PauseRequested` from `Step.__enter__` for a non-exempt node and never for an exempt one; the worker parks `paused`, clears the flag, writes `run.paused`, and a resume continues; the stale flag cleared on park; auto replay enqueued on seal when the setting is on and not when off, and never for a replay; `create_run_from_brief` and `create_replay` set `trace_id`; `node.*` and `tool.*` spans with the in-memory exporter; the CLI `pause` command and `resume` from `paused` through the subprocess CLI.
- API: section 3.10.
- Console: section 6.5.
- End to end: the Polish fake run through the API and the console is checked by hand for the done criteria (the hand-off note records it); the existing Polish CLI test stays.

## 10. Risks

- **Pause between a gate's answer and its step.** Handled by exempting the gate nodes; the implementer verifies LangGraph 1.2.20's exception propagation from a node (plain exception or group) with a throwaway graph before wiring the worker, and records the result.
- **SSE under uvicorn.** An async generator with `to_thread` reads holds no thread between polls, but each poll takes a pooled connection for a few milliseconds; the pool of 16 serves the demo. The implementer checks `request.is_disconnected()` ends the stream within one poll interval.
- **Phoenix and Jaeger image tags.** Pinned to the releases current at implementation time, recorded in the hand-off note; the OTLP/HTTP paths (`/v1/traces` on 4318 and on 6006) are verified against the running containers during the demo verification.
- **Console build in the image.** `npm ci` needs the committed `package-lock.json`; the Docker job in CI proves the stage.
- **OpenAPI types drift.** The `console-types` CI check catches it; the fixtures are refreshed by hand and the API tests validate the live shapes.
- **Checkpoint reads in the API.** `run_refs` compiles the graph per call (milliseconds) and reads the checkpoint with the API's pool; the run detail endpoint makes one such read.
- **Shared development database.** The worktree's tests use temporary databases on the development Postgres. Compose publishes Postgres on `${MODELFORGE_POSTGRES_PORT:-5432}` so a second stack can run beside the SP2 project's: the demo verification in this worktree uses `MODELFORGE_POSTGRES_PORT=5433` and `docker compose -p modelforge-sp5`, and the other worktrees' stack is untouched (the API, Jaeger and Phoenix ports are free on this machine).

## 11. Umbrella spec amendments to record (closing docs commit, section 22)

Listed in section 8's last bullet.

## 12. Deferred and out of scope

Section 1's list, plus: a worker lease for a run whose worker died (status stays `running`; the console shows it and `abort` stays refused); CORS (same origin or the Vite proxy); authentication beyond the header; a scheduled replay job; Phoenix trace deep links; a chart library; MSW; server-side pagination of steps, artifacts and findings (bounded per run).

## 13. As-built amendments

Decisions made during execution that change or sharpen the text above; each is recorded with its reasoning in `docs/superpowers/notes/2026-11-10-sp5-handoff.md`.

- 3.5: SSE frames carry `id:` and `data:` only, with no `event:` line; the event type is read from `data.type`. A named frame reaches only an `EventSource` listener registered for that name, so an unnamed one is the only form that never drops a type the client did not list.
- 3.5: the global stream re-reads a trailing window of 200 ids behind its cursor (`sse.WINDOW`) on every poll and on a reconnect, and skips ids it has delivered. Event ids come from a sequence at insert but commits are serialised only per run, so across runs a lower id can commit after a higher one was read. Limitation: an event that commits out of order more than 200 ids behind the cursor is missed, and a reconnect may repeat events (the console's global hook only invalidates queries). The per-run stream is exact, because a run's event ids are serialised.
- 3.3: `RunDetail.replays` items, like `GET /runs/{id}/replays`, are `{run: RunRecord, report: ReplayReport | null}` rather than `{run_id, status, rerun_llm, report}`.
- 3.4 and 4.2: `runs.request_pause` is one conditional update that sets the flag only while the run is `running`, so a run that parks or finishes concurrently is never flagged after its worker cleared the flag. `runs.transition` (a status change guarded by the statuses it may move from) and `queue.enqueue_if_absent` back the API's abort, resume and approvals and the worker's claim: a second gate decision while one is queued is refused with `409` kind `approval`, a resume while one is queued with `409` kind `state`, and a concurrent abort cannot be lost to a claim. The CLI's `approve`, `resume` and `abort` use the same functions.
- 5.2: spans record a failure as an `exception` event carrying the exception type (and the gateway's error kind or the tool's error code, also set as `modelforge.error_kind` or `modelforge.error_code`) only, not `record_exception`'s message and stack trace, because those can quote prompt, brief or data text. With `MODELFORGE_TRACE_CONTENT=1` the node and LLM spans use `record_exception`; tool spans never do.
- 6.4: `modelforge-api fixtures` normalises every machine-specific value: directories become `<dir>` and artifact URIs `artifact://<id>`, so the committed fixtures are the same on any machine.
- 7: Phoenix 21 enables authentication by default, so Compose sets `PHOENIX_ENABLE_AUTH=false` for the local collector. The images are pinned to `jaegertracing/jaeger:2.23.0`, `arizephoenix/phoenix:version-21.0.0` and, for the console build stage, `node:22.23.4-alpine`.
- 3.2: `MODELFORGE_USERS` naming the same identity twice is a configuration error, like a malformed entry or an unknown role; an empty value counts as unset (every identity has every role).
- 3.10: `packages/api/tests` has no `__init__.py`. The plan's one made the API's `conftest.py` shadow the core package's under pytest's importlib mode.
- 2: confirmed as built: the console lives at `console/`, not the umbrella's `ui/`, and the layering is `(api | evals) > orchestrator > (mcp | gateway) > knowledge > lineage > core` with nine import-linter contracts.
- 3.8: `GET /settings` returns `profiles` beside `config` (`{config, profiles}`), the plan's Task 14 shape, rather than inside it as written above.
- 4.2: the worker releases its queue row and clears the pause flag in the same transaction that writes the parked or finished status (gate, finish, paused, needs_attention), so a decision or resume that lands right after the park is never refused as already queued; the `finally` release remains as the backstop and runs only when no outcome was written, since it would otherwise delete that newer row. The needs_attention branch records the failure on the span only after the lineage writes.
- 3.4: `resume` (API and CLI) reads the run `for update` inside its transaction before checking the status, so a concurrent abort cannot leave a resume row on an aborted run.
- 3.3: JSON and text artifact content is served as the stored bytes, not parsed and re-serialised; every artifact body and the `html` and `md` document responses carry `Content-Security-Policy: sandbox` and `X-Content-Type-Options: nosniff`; a manifest or replay report missing from the store is `404` kind `not_found` (for a replay report, on `GET /runs/{id}` as well, since the detail lists the replays); the `GET /health` 503 body names the exception type only.
- 10.1 (umbrella): `migrate.apply` holds a transaction-scoped advisory lock (`pg_advisory_xact_lock`) while it reads and applies migrations, so the API and the worker migrating a fresh database at first boot both succeed and each migration runs once.
- 6.3: the Replays panel's badge prefers the latest completed plain replay whose report is `ok` ("Reproduced: N artifacts matched") over an in-flight replay, which it prefers over the latest other replay.
- 3.9: the `by_purpose` and `by_profile` rows of `GET /costs` carry `input_tokens` and `output_tokens` like `by_model`.
- 3.10 and 5.3: `modelforge_lineage.observability.get_logger` returns structlog's lazy proxy, so a module-level logger created before `configure_logging` follows it (in production as in tests); the api tests configure logging once per session and the lineage observability test restores structlog's configuration, so the full suite passes in any order.
