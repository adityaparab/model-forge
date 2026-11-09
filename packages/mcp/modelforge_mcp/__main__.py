"""`modelforge-mcp <command>`: serve one MCP server over stdio, or write tool contracts."""

import argparse
import os
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import get_args

from modelforge_lineage.observability import configure_logging

from modelforge_mcp import contracts
from modelforge_mcp.common import ServerName, settings_from_env
from modelforge_mcp.registry import build_server

SERVER_NAMES: tuple[ServerName, ...] = get_args(ServerName)


def _serve(name: ServerName) -> int:
    configure_logging(os.environ.get("MODELFORGE_LOG_LEVEL", "INFO"))
    build_server(name, settings_from_env()).run("stdio")
    return 0


def _serve_command(name: ServerName) -> Callable[[], int]:
    return lambda: _serve(name)


def _print_paths(paths: Sequence[Path]) -> int:
    for path in paths:
        print(path)
    return 0


def _contracts(out: Path | None) -> int:
    return _print_paths(contracts.write_snapshots(out or contracts.CONTRACTS_DIR))


def _goldens(artifact_dir: Path | None) -> int:
    settings = settings_from_env().model_copy(
        update={"artifact_dir": artifact_dir or Path(tempfile.mkdtemp())}
    )
    return _print_paths(contracts.write_goldens(settings))


COMMANDS: dict[str, Callable[[], int]] = {name: _serve_command(name) for name in SERVER_NAMES}
TOOL_COMMANDS = ("contracts", "goldens")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="modelforge-mcp", description=__doc__)
    parser.add_argument("command", help=f"one of: {', '.join([*COMMANDS, *TOOL_COMMANDS])}")
    parser.add_argument("--out", type=Path, help="contracts: snapshot output directory")
    parser.add_argument("--artifact-dir", type=Path, help="goldens: artifact store directory")
    parser.add_argument("--marker", help="ignored; tags a server process so its owner can find it")
    args = parser.parse_args(argv)
    if args.command == "contracts":
        return _contracts(args.out)
    if args.command == "goldens":
        return _goldens(args.artifact_dir)
    command = COMMANDS.get(args.command)
    if command is None:
        print(
            f"unknown command {args.command!r}; servers: {', '.join(SERVER_NAMES)}", file=sys.stderr
        )
        return 2
    return command()


if __name__ == "__main__":
    sys.exit(main())
