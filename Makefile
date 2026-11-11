.PHONY: lint format test test-fast sync example db-up db-down goldens migrate worker up down evals evals-live evals-baseline console console-test console-types console-fixtures scripts-install docs-check results results-check ui-dev api demo demo-down demo-record fresh-clone-test

PROFILE ?= default

lint:
	uv run ruff check packages examples && uv run ruff format --check packages examples && uv run pyright && uv run lint-imports && npm run typecheck --prefix console && npm run lint --prefix console

format:
	uv run ruff format packages examples && uv run ruff check --fix packages examples

test:
	uv run pytest && npm test --prefix console

test-fast:
	uv run pytest -m "not slow"

sync:
	uv sync --all-groups && npm ci --prefix console

example:
	uv run python examples/core_pipeline.py

db-up:
	docker compose up -d --wait postgres

db-down:
	docker compose down

goldens:
	uv run modelforge-mcp goldens

migrate:
	uv run modelforge migrate

worker:
	uv run modelforge worker

up:
	docker compose up -d --build --wait

down:
	docker compose down

evals:
	uv run modelforge-evals run --profile fake --baseline evals/baselines/fake.json

evals-live:
	uv run modelforge-evals run --profile $(PROFILE) --baseline evals/baselines/$(PROFILE).json

evals-baseline:
	uv run modelforge-evals run --profile fake --write-baseline evals/baselines/fake.json

console:
	npm run build --prefix console

console-test:
	npm test --prefix console

console-types:
	uv run modelforge-api openapi > console/openapi.json && npm run types --prefix console

console-fixtures:
	uv run modelforge-api fixtures --out console/src/fixtures

scripts-install:
	npm ci --prefix scripts

docs-check:
	uv run python scripts/check_docs.py

RESULTS_DOCS = README.md docs/trade-offs.md docs/results/README.md

results:
	uv run python scripts/record_result.py render $(RESULTS_DOCS)

results-check:
	uv run python scripts/record_result.py render --check $(RESULTS_DOCS)

ui-dev:
	npm run dev --prefix console

api:
	uv run modelforge-api

demo:
	docker compose up -d --build --wait
	@for i in $$(seq 60); do curl -sf http://localhost:8000/api/v1/health | grep -q '"ok"' && break; sleep 1; done
	@id=$$(curl -sf -X POST 'http://localhost:8000/api/v1/runs?profile=fake' -H 'X-ModelForge-User: alice' -H 'Content-Type: application/yaml' --data-binary @examples/briefs/polish-pd.yaml | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])') \
		&& echo "run $$id" && echo "http://localhost:8000/runs/$$id" \
		&& { [ -n "$$MODELFORGE_DEMO_NO_OPEN" ] || (open "http://localhost:8000/runs/$$id" || xdg-open "http://localhost:8000/runs/$$id") >/dev/null 2>&1 || true; }

demo-down: down

# a stack of its own (project modelforge-record, Postgres on 5433), always torn down with -v
demo-record:
	@export COMPOSE_PROJECT_NAME=modelforge-record MODELFORGE_POSTGRES_PORT=5433 MODELFORGE_AUTO_REPLAY=1; \
	trap 'docker compose down -v' EXIT; \
	docker compose up -d --build --wait \
		&& for i in $$(seq 120); do curl -sf http://localhost:8000/api/v1/health | grep -q '"ok"' && break; sleep 1; done \
		&& node scripts/demo_record.mjs --out docs/media

fresh-clone-test:
	bash scripts/fresh_clone_test.sh
