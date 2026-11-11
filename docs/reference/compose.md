This page is the reference for the Docker Compose stack; the README is the front door.

# Compose

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

[Back to the README](../../README.md)
