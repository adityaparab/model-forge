This page is the reference for the make targets and commands; the README is the front door.

# Commands

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

[Back to the README](../../README.md)
