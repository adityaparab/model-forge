"""`modelforge-mcp <command>`: serve one MCP server over stdio."""

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from typing import get_args

from modelforge_lineage.observability import configure_logging

from modelforge_mcp.common import ServerName, settings_from_env
from modelforge_mcp.registry import build_server

SERVER_NAMES: tuple[ServerName, ...] = get_args(ServerName)


def _serve(name: ServerName) -> int:
    configure_logging(os.environ.get("MODELFORGE_LOG_LEVEL", "INFO"))
    build_server(name, settings_from_env()).run("stdio")
    return 0


def _serve_command(name: ServerName) -> Callable[[], int]:
    return lambda: _serve(name)


COMMANDS: dict[str, Callable[[], int]] = {name: _serve_command(name) for name in SERVER_NAMES}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="modelforge-mcp", description=__doc__)
    parser.add_argument("command", help=f"one of: {', '.join(COMMANDS)}")
    args = parser.parse_args(argv)
    command = COMMANDS.get(args.command)
    if command is None:
        print(
            f"unknown command {args.command!r}; servers: {', '.join(SERVER_NAMES)}", file=sys.stderr
        )
        return 2
    return command()


if __name__ == "__main__":
    sys.exit(main())
