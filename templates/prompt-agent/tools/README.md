# Prompt agent + function tools

A declarative Foundry prompt agent that declares **client-executed function tools**. The schema lives in
the agent; the code that runs the function lives in *your application*. Adapted from upstream
`cli/prompt-agents/02-tools`.

## What changed from upstream

Only the model: `model: ai-gateway/<logical-model>` and no model deployment (R1, R2). The upstream
`get_weather` and `convert_currency` become `get_ticket_status` and `convert_currency`, both read-only.

## What this means for the gateway rules

The model emits a `function_call`; nothing runs inside Foundry. So the rule moves to your application:

- The function body must reach backends **only through the gateway Tools and MCP API** (R4), with the
  gateway holding any credential (R5). A function that calls a SaaS API directly is the bypass.
- If the function calls a model, it uses the gateway Models API, not a project endpoint (R1).
- If a tool is better shared across agents, make it an MCP server (see `../mcp`) instead of a function.

## Fill in

`GATEWAY_MODEL_NAME`. Replace the two sample functions with yours; keep `strict: true` and
`additionalProperties: false`. The sample ships no application code, so an invoke returns a
`function_call` item and no text until your client submits `function_call_output`.

## Deploy (dev only)

```bash
azd env set GATEWAY_MODEL_NAME ...
azd up
azd ai agent invoke --new-conversation "What is the status of ticket T-1042?"
```

## Status

Not deployed or run. The YAML parses and passes `../../_gateway/check_templates.py`. The lint cannot see
inside your application's function code; review that by hand.
