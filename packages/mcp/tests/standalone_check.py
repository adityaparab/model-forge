"""Start `modelforge-mcp data-profiler` as a subprocess over stdio and print its tool names.

Not collected by pytest (no `test_` prefix). Run:
    MODELFORGE_ARTIFACT_DIR=/tmp/mf-artifacts uv run python packages/mcp/tests/standalone_check.py
"""

import asyncio
import os

from mcp import Client, StdioServerParameters


async def _tool_names() -> list[str]:
    params = StdioServerParameters(
        command="uv", args=["run", "modelforge-mcp", "data-profiler"], env=dict(os.environ)
    )
    async with Client(params) as client:
        return [tool.name for tool in (await client.list_tools()).tools]


def main() -> int:
    for name in asyncio.run(_tool_names()):
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
