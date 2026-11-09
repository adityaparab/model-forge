"""Aggregate every server's tool specs and builders; the single lookup clients and tests use."""

from collections.abc import Callable

from mcp.server.mcpserver import MCPServer

from modelforge_mcp.common import ServerName, ServerSettings, ToolSpec
from modelforge_mcp.servers import credit_modeling, data_profiler, doc_renderer, validation

__all__ = ["SERVERS", "TOOLS", "ToolSpec", "build_server"]

_MODULES = [data_profiler, credit_modeling, validation, doc_renderer]

TOOLS: dict[tuple[ServerName, str], ToolSpec] = {
    (spec.server, spec.tool): spec for module in _MODULES for spec in module.TOOL_SPECS
}
SERVERS: dict[ServerName, Callable[[ServerSettings], MCPServer]] = {
    module.SERVER_NAME: module.build for module in _MODULES
}


def build_server(name: ServerName, settings: ServerSettings) -> MCPServer:
    try:
        builder = SERVERS[name]
    except KeyError:
        raise KeyError(f"unknown server {name!r}; known: {sorted(SERVERS)}") from None
    return builder(settings)
