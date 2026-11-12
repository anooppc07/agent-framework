# Prompt agent + Foundry Toolbox

A prompt agent whose tools come from a **Foundry Toolbox**: one versioned set of tools shared by agents.
Adapted from upstream `cli/prompt-agents/04-foundry-toolbox`.

## What changed from upstream, and why

| Upstream | Here | Rule |
|---|---|---|
| Toolbox holds built-in `web_search` | Toolbox holds only MCP tools published on the gateway; no `web_search`, `code_interpreter` | R4 |
| `model: gpt-5.4-mini` + deployment | `model: ai-gateway/<logical-model>`; no deployment | R1, R2 |
| (none) | `RemoteTool` connection, `ProjectManagedIdentity`, gateway target | R4, R5 |
| `require_approval: never` | `always` on the agent, per-tool `never` only inside the toolbox for read-only tools | R6 |
| (none) | `defaultConfig.enabled: false` turns off the harness's own built-in toolset | R4 |

## Why a toolbox

07 recommends it for PromptOps: promote a new toolbox version and every agent that uses the default
version gets it. That is also the risk: a change reaches every agent at once, so review toolbox changes
like a code change and pin versions where you can (07 lists "can an agent pin a toolbox version" as a PoC item).

## Exception to sign off

Toolboxes on prompt agents need `harness: microsoft_copilot_preview`, a preview feature. Listed in
`../../_gateway/GATEWAY-CONTRACT.md`. Confirm it is available in the SCBX region before relying on it.

## Fill in

`GATEWAY_MCP_BASE_URL`, `MCP_SERVER_PATH`, `GATEWAY_TOKEN_AUDIENCE`, `GATEWAY_MODEL_NAME`; replace `lookup`
with the registry's tool names.

## Deploy (dev only)

```bash
azd env set GATEWAY_MODEL_NAME ...   # and the other three
azd up
azd ai agent invoke "Look up key abc using the toolbox."
```

## Status

Not deployed or run. The YAML parses and passes `../../_gateway/check_templates.py`. The `connection:` key
inside a toolbox tool is copied from upstream's hosted-agent toolbox sample; confirm it applies to prompt
agents too.
