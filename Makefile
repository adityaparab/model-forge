.PHONY: lint format test test-fast sync

lint:
	uv run ruff check packages && uv run ruff format --check packages && uv run pyright && uv run lint-imports

format:
	uv run ruff format packages && uv run ruff check --fix packages

test:
	uv run pytest

test-fast:
	uv run pytest -m "not slow"

sync:
	uv sync --all-groups
