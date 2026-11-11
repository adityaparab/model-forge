This page is the reference for the HTTP API; the README is the front door.

# API

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
| `GET /runs/{id}/findings` | the findings, their evidence citations resolved as in the document, the remediation responses and a summary |
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

**Event streams.** `GET /runs/{id}/events/stream` and `GET /events/stream` are server-sent event streams of lineage events. Each message carries `id:` (the event id) and `data:` (the event record as JSON) and no `event:` line, so a browser `EventSource` delivers every message to `onmessage`; read the event type from `data.type`. A stream resumes after the `Last-Event-ID` header or an `?after=<id>` parameter; without either, the run stream starts at the run's first event and the global stream at the current head. An id above the newest event (left from before a database reset) starts the run stream from the beginning and the global stream at the head. The API polls the events table every 0.5 s and sends a keepalive comment after 15 s of silence; there is no `LISTEN`/`NOTIFY`. The run stream is exact, because a run's events are written one at a time. Across runs a lower event id can commit after a higher one was already sent, so the global stream re-reads a trailing window of 200 ids behind its cursor and skips ids it has sent; an event that commits more than 200 ids out of order is missed, and a reconnect may repeat events. The console uses the global stream only to refresh the run list, so neither matters there.

```bash
curl -N http://localhost:8000/api/v1/runs/<run_id>/events/stream
```

[Back to the README](../../README.md)
