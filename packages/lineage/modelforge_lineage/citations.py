"""Citation path grammar over artifact JSON, and the number formatting citations render with.

Standard library only, so every higher package can use it.
"""

import math
from typing import Any

DISCRIMINATORS: tuple[str, ...] = ("split", "test_id", "feature", "grade", "name")


class PathError(KeyError):
    pass


def parse_path(path: str) -> list[str]:
    """Split ``path`` on ``.`` outside brackets; ``[...]`` is one verbatim step.

    ``tests[disc.auc.oot].value`` gives ``["tests", "disc.auc.oot", "value"]``.
    """
    steps: list[str] = []
    i = 0
    while True:
        if path.startswith("[", i):
            end = path.find("]", i + 1)
            if end == -1:
                raise PathError(path)
            step, i = path[i + 1 : end], end + 1
        else:
            j = i
            while j < len(path) and path[j] not in ".[":
                j += 1
            step, i = path[i:j], j
        if not step:
            raise PathError(path)
        steps.append(step)
        if i == len(path):
            return steps
        if path[i] == ".":
            i += 1
        elif path[i] != "[":
            raise PathError(path)


def _select(items: list[Any], step: str, path: str) -> Any:
    """The element whose discriminator equals ``step``, discriminators tried in order."""
    for disc in DISCRIMINATORS:
        for element in items:
            if isinstance(element, dict) and disc in element and str(element[disc]) == step:
                return element
    raise PathError(path)


def resolve_path(value: Any, path: str) -> Any:
    """Resolve ``path`` against ``value``; any step that does not apply is ``PathError``."""
    current = value
    for step in parse_path(path):
        if isinstance(current, dict):
            if step not in current:
                raise PathError(path)
            current = current[step]
        elif isinstance(current, list):
            current = len(current) if step == "count" else _select(current, step, path)
        else:
            raise PathError(path)
    return current


def format_number(x: float | int | str | bool | None) -> str:
    """4 significant figures for floats, plain digits for ints, ``n/a`` otherwise.

    ``n/a`` covers absent values, non-finite floats, booleans and strings.
    """
    if x is None or isinstance(x, (str, bool)):
        return "n/a"
    if isinstance(x, int):
        return str(x)
    if not math.isfinite(x):
        return "n/a"
    return f"{x:.4g}"
