This page is the reference for the MCP servers; the README is the front door.

# MCP servers

The toolkit is exposed through five MCP servers over stdio: `data-profiler`, `credit-modeling`, `validation`, `doc-renderer` and `knowledge`. Each runs with:

```bash
uv run modelforge-mcp <server>
```

They read `MODELFORGE_ARTIFACT_DIR` (the artifact store) and `MODELFORGE_DATA_DIR` (the dataset cache); `doc-renderer` also reads `MODELFORGE_TEMPLATES_DIR`, and `knowledge` reads its corpus from `MODELFORGE_KNOWLEDGE_DIR`. To use a server from a desktop MCP client, add it to the client's configuration, for example:

```json
{
  "mcpServers": {
    "modelforge-credit-modeling": {
      "command": "uv",
      "args": ["run", "--project", "<repo>", "modelforge-mcp", "credit-modeling"],
      "env": {
        "MODELFORGE_ARTIFACT_DIR": "<repo>/artifacts",
        "MODELFORGE_DATA_DIR": "<repo>/data"
      }
    }
  }
}
```

The orchestrator starts the servers itself as `python -m modelforge_mcp <server>`. A custom `MODELFORGE_MCP_COMMAND` must accept a trailing `<server> --marker <uuid>` argument pair: the worker tracks the server processes it started by that marker. `make goldens` regenerates the golden artifacts.

[Back to the README](../../README.md)
