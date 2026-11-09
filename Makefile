.PHONY: lint format test test-fast sync example

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
