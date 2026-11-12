# SCBX agent templates

Seven starting points for Foundry agents that follow the SCBX architecture (`docs/architecture-kb/`): agents reach
models and tools **only through the AI Gateway**. Adapted from
[microsoft-foundry/foundry-samples](https://github.com/microsoft-foundry/foundry-samples) (MIT).

**Start with** [`_gateway/GATEWAY-CONTRACT.md`](_gateway/GATEWAY-CONTRACT.md): the rules (R1-R10), the environment variables
DataX must supply, and the exceptions that need sign-off.

## Which template

| I need... | Use | Kind | Status |
|---|---|---|---|
| a no-code agent that calls tools from one MCP server on the gateway | [`prompt-agent/mcp`](prompt-agent/mcp) | prompt, `azd` YAML | written, not deployed |
| a no-code agent with functions my app runs | [`prompt-agent/tools`](prompt-agent/tools) | prompt, `azd` YAML | written, not deployed |
| a no-code agent whose tools come from a shared, versioned toolbox | [`prompt-agent/toolbox`](prompt-agent/toolbox) | prompt, `azd` YAML | written, not deployed; **preview harness** |
| my own code, with traces and one trace id end to end | [`hosted-agent/observability`](hosted-agent/observability) | hosted, MAF Python | **run locally** |
| a draft, a human review, then approve / revise / reject | [`hosted-agent/human-in-the-loop`](hosted-agent/human-in-the-loop) | hosted, MAF Python | **run locally** |
| the agent to remember facts per user across sessions | [`hosted-agent/foundry-memory`](hosted-agent/foundry-memory) | hosted, MAF Python | partly run; **memory path not run** |
| conversation state in a store the PortCo owns | [`hosted-agent/custom-store`](hosted-agent/custom-store) | hosted, MAF Python | **run locally on real Redis** |

Prompt agent or hosted agent: if behaviour fits in instructions plus tools, use a prompt agent; if you need your own code,
loops, a workflow or custom state, use a hosted agent. A prompt agent has no code to run, which is why those three are
`azd` manifests and not MAF code.

## What each template does about the architecture

| Rule | How the templates meet it |
|---|---|
| No direct model call (R1-R3) | Prompt agents use `model: ai-gateway/<model>`; hosted agents build their client with [`_gateway/gateway_client.py`](_gateway/gateway_client.py) |
| No direct MCP / tool call (R4-R6) | MCP URLs are `${GATEWAY_MCP_BASE_URL}/...` through a project connection; tools allow-listed; built-in `web_search` / `code_interpreter` removed; no stored keys |
| Content stays in the PortCo (R7) | Both content variables set to false (see below) |
| One trace id (R8) | `traceparent` forwarded to the gateway |
| Private by default (R9) | Redis Bicep is private; nothing here opens a public endpoint |
| Dev only (R10) | Deploy to a dev project; prod goes through the shared DataX pipeline |

## Check a template did not regress

```bash
python _gateway/check_templates.py .        # 0 = clean, 1 = findings, 2 = nothing found
```

It flags a direct model client, a non-gateway MCP URL, stored credentials, built-in tools, bare `require_approval: never`,
`ENABLE_SENSITIVE_DATA: true` and public Redis. Run against the unmodified upstream samples it reports 25 findings.
**It is a regression lint, not enforcement** (private endpoints and the firewall are), and **the lint itself has no tests yet**.

## Run a hosted template without Azure

[`_gateway/fake_gateway.py`](_gateway/fake_gateway.py) stands in for the gateway (Models API plus one MCP tool) and logs each
request's path, whether a bearer token arrived, and the `traceparent`. Each hosted README has the exact commands.
It is not a model; it echoes your message, and `CALL <tool> {json}` makes it request a tool call.

## Things we found that you should know

1. **The hosting package ignores `ENABLE_SENSITIVE_DATA`.** It reads `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`,
   defaults it to `true`, and records prompts, responses and tool arguments in traces. Every hosted template sets both to false.
   KB 08 says content recording is "off (default)"; for hosting `1.0.0b261106` that is not true.
2. **Upstream samples bypass the gateway** in ways the lint finds: public MCP URLs, built-in tools, `FoundryChatClient` on the
   project endpoint, a stored GitHub token, `ENABLE_SENSITIVE_DATA: 'true'`, Redis with public access.
3. **The human-in-the-loop wire format differs from LangGraph's** (a `function_call` named `request_info`, not an
   `mcp_approval_request`), and reject is a normal marked result because this host turns any exception into a generic failure.
4. **Memory and replayed approvals fail quietly.** A broken memory store still answers `completed`; a replayed approval returns
   empty output with no error.

## Not covered (out of scope for this cut)

Evals, CI/CD pipelines, registration in API Center, cross-PortCo calls, Content Safety and PII (gateway policies), and tests for
the lint. No template has been deployed: `azd` and the Bicep CLI are not installed where these were written, and no
real gateway URL, token scope or audience is known (the KB does not give them).
