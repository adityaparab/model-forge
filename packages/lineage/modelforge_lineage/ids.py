"""Prefixed random identifiers: ``run_<12 hex>``, ``step_<12 hex>`` and so on."""

from uuid import uuid4


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"
