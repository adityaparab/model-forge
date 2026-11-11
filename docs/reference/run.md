This page is the reference for running a governed model build from the command line, the API and the console; the README is the front door.

# Run a governed model build

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

[Back to the README](../../README.md)
