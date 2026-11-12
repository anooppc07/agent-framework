# Hosted agent: observability

An Agent Framework agent (`kind: hosted`, Responses 2.0.0) with OpenTelemetry on, that reaches its
model **through the AI Gateway**. Adapted from upstream `agent-framework/responses/08-observability`.

## What changed from upstream, and why

| Upstream | Here | Rule |
|---|---|---|
| `FoundryChatClient(project_endpoint=...)` | `gateway_client.chat_client()`: OpenAI-compatible client on the gateway Models API, Entra token | R1, R3 |
| model deployment in the project | none; the logical model name comes from the gateway | R1 |
| `ENABLE_SENSITIVE_DATA: 'true'` | `'false'` **and** `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT: 'false'` | R7 |
| (none) | `traceparent` is forwarded to the gateway | R8 |

### The content-recording trap

`ENABLE_SENSITIVE_DATA=false` is **not enough**. With hosting `1.0.0b261106` the host reads
`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`, defaults it to `true`, and then switches MAF's content
capture on regardless. Verified here by running the agent: with only `ENABLE_SENSITIVE_DATA=false`, every
span carried `gen_ai.input.messages`, `gen_ai.output.messages` and the tool arguments and results, so prompts
and responses would have gone to App Insights. With the OTEL variable also set to `false`, they are gone.
Keep both lines in `azure.yaml`. Re-check after any hosting-package bump.

## Fill in (DataX gives you these; none is in the architecture KB)

`GATEWAY_MODELS_BASE_URL`, `GATEWAY_MODEL_NAME`, `GATEWAY_TOKEN_SCOPE`. Replace `get_ticket_status` with an MCP
tool from `gateway_client.mcp_tool(...)`; do not call a backend directly from a function (R4, R5).

## Run locally against the fake gateway (no Azure needed)

```bash
python ../../_gateway/fake_gateway.py 8099 &
cd src/agent
export GATEWAY_MODELS_BASE_URL=http://127.0.0.1:8099/v1 GATEWAY_MODEL_NAME=logical-model \
       GATEWAY_TOKEN_SCOPE=https://management.azure.com/.default \
       ENABLE_INSTRUMENTATION=true ENABLE_SENSITIVE_DATA=false \
       OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false
uv sync && uv run python main.py        # Responses endpoint on http://localhost:8088/responses
curl -X POST localhost:8088/responses -H 'content-type: application/json' \
     -H 'traceparent: 00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01' \
     -d '{"input":"hello","conversation":{"id":"c1"}}'
```

The fake gateway prints one line per request: path, whether a bearer token arrived, and the `traceparent`.
The trace ID in it should equal the one you sent.

## Deploy (dev project only)

`azd env set` the three variables, then `azd up` and `azd ai agent invoke "hello"`. Traces then appear in the
project's Application Insights (Foundry Traces tab). To copy metrics to ELK, follow KB 06 option 3.

## Status

Verified locally with `curl` against the fake gateway: model call reached the gateway with a bearer token,
the trace ID propagated, the tool call completed, no message content in span output. **Not deployed:** `azd`
is not installed here, and nothing was run against Foundry or a real gateway. `uv.lock` is intentionally absent;
see `src/agent/uv.toml`.
