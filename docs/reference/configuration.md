This page is the reference for configuration; the README is the front door.

# Configuration

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

[Back to the README](../../README.md)
