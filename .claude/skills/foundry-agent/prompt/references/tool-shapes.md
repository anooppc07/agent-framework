---
description: 'Exact tools-array JSON blocks for each grounding option in the foundry-agent skill (prompt type)'
---

# Tool Shapes

The `definition.tools` array wires the agent's grounding and action surface. Pick the block that
matches the caller's `grounding` choice and substitute it for `{{TOOLS_BLOCK}}` in
`<name>.agent.json`. Keep connection ids, server URLs, and keys as `__DEPLOY_TIME__` placeholders —
never hardcode a real value.

## `grounding: fabric` — Microsoft Fabric data agent (`fabric_iq_preview`)

Binds a published Fabric data agent reached through a `fabric_iq_preview` (RemoteTool) project
connection. Foundry brokers the Fabric credential **On-Behalf-Of the signed-in caller**, so each run
is authorized and attributed to the person who made it. An *unauthenticated* call returns an empty
successful run (not an error) — do not mistake that for a broken tool.

```json
"tools": [
  {
    "type": "fabric_iq_preview",
    "project_connection_id": "__FABRIC_CONNECTION_ID__",
    "server_url": "__FABRIC_SERVER_URL__",
    "require_approval": "always"
  }
]
```

> `require_approval` defaults to `always` in this skill: every tool call waits for approval before it
> runs. Set it to `never` only for a tool the owning team has agreed is safe to run unattended.

> Do **not** use the `fabric_dataagent_preview` (CustomKeys) shape for an OBO data agent. A
> `TokenIsMissing` error is the OBO empty/unauthenticated-run symptom, not a wrong-shape symptom.
> Test with a real bearer token: `az account get-access-token --resource https://ai.azure.com`.

The deploy script requires `FabricConnectionId` and `FabricServerUrl`.

## `grounding: mcp` — remote MCP server

Binds a remote Model Context Protocol server as a tool. Use for any MCP-exposed backend.

```json
"tools": [
  {
    "type": "mcp",
    "server_label": "{{MCP_SERVER_LABEL}}",
    "server_url": "__MCP_SERVER_URL__",
    "require_approval": "always"
  }
]
```

> `require_approval` defaults to `always` in this skill: every tool call waits for approval before it
> runs. Set it to `never` only for a tool the owning team has agreed is safe to run unattended.

The deploy script requires `McpServerUrl` (and may pass headers/connection id depending on the
server's auth).

## `grounding: custom` — function tool

Declares a function tool the model can call. The function itself is implemented by the consuming
application (the agent only declares the contract).

```json
"tools": [
  {
    "type": "function",
    "name": "{{FUNCTION_NAME}}",
    "description": "{{FUNCTION_DESCRIPTION}}",
    "parameters": {
      "type": "object",
      "properties": {
        "{{PARAM_NAME}}": { "type": "string", "description": "{{PARAM_DESCRIPTION}}" }
      },
      "required": ["{{PARAM_NAME}}"]
    }
  }
]
```

## `grounding: none` — model-only

No tools. The agent answers from the model and its instructions only.

```json
"tools": []
```

## Eval implications

| grounding | groundedness evaluators | tool evaluators | dataset `context` | dataset `tool_definitions` |
|-----------|-------------------------|-----------------|-------------------|----------------------------|
| `fabric`  | keep | keep (name = `DataAgent_<FabricConnectionName>`) | required for grounded cases | required |
| `mcp`     | keep | keep (name = the MCP tool's runtime name) | required for grounded cases | required |
| `custom`  | optional | keep (name = the function name) | optional | required |
| `none`    | drop | drop | drop | drop |

The tool evaluators' `tool_definitions[].name` **must** be the exact tool name the agent calls at
runtime, not a descriptive label. For Fabric that is `DataAgent_<FabricConnectionName>`. A mismatch
makes `tool_call_accuracy` return "Not applicable" and scores the wrong tool.
