This page is the reference for the console; the README is the front door.

# Console

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

[Back to the README](../../README.md)
