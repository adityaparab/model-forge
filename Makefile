.PHONY: lint format test test-fast sync example db-up db-down goldens migrate worker up down

lint:
	uv run ruff check packages examples && uv run ruff format --check packages examples && uv run pyright && uv run lint-imports

format:
	uv run ruff format packages examples && uv run ruff check --fix packages examples

test:
	uv run pytest

test-fast:
	uv run pytest -m "not slow"

sync:
	uv sync --all-groups

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
