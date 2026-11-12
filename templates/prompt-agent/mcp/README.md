# Prompt agent + MCP

A declarative Foundry prompt agent (`kind: prompt`) that uses **one MCP server published on the AI Gateway**.
Adapted from upstream `cli/prompt-agents/03-mcp-tools`.

## What changed from upstream, and why

| Upstream | Here | Rule (GATEWAY-CONTRACT.md) |
|---|---|---|
| `model: gpt-5.4-mini` plus a model deployment in the project | `model: ai-gateway/<logical-model>`; no deployment | R1, R2 |
| `server_url: https://learn.microsoft.com/api/mcp` | `${GATEWAY_MCP_BASE_URL}/${MCP_SERVER_PATH}` via a `RemoteTool` connection | R4 |
| `require_approval: never` for every tool | `never` only for the named read-only tool; anything else asks | R6 |
| no tool allow-list | `allowed_tools: [lookup]` | 02 "per-agent tool allow-list" |
| (none) | connection uses `ProjectManagedIdentity`; no key in the file | R5 |

## Fill in (DataX gives you these; none is in the architecture KB)

`GATEWAY_MCP_BASE_URL`, `MCP_SERVER_PATH`, `GATEWAY_TOKEN_AUDIENCE`, `GATEWAY_MODEL_NAME`. Then replace
`lookup` with the tool names the MCP registry lists for your server. Tools that write must not be under `never`.

## Deploy (dev project only; prod is reached through the gateway and the shared pipeline)

```bash
azd env set GATEWAY_MCP_BASE_URL ...    # and the other three
azd up
azd ai agent invoke "Look up key abc."
```

## Status

Not deployed or run: `azd` is not installed where this was written. The YAML parses and passes
`../../_gateway/check_templates.py`. Whether Foundry accepts the connection `audience` and the
`ai-gateway/` model prefix on your tenant is unverified.
