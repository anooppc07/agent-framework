# Copyright (c) Microsoft. All rights reserved. (client pattern adapted from microsoft-foundry/foundry-samples, MIT)
"""The one way a hosted SCBX agent reaches models and tools: through the PortCo's AI Gateway.

Canonical copy. Every hosted template vendors an identical copy in its src/agent folder so the
folder deploys on its own. Keep them identical:  fc /b _gateway\\gateway_client.py <t>\\src\\agent\\gateway_client.py
Env vars and what the gateway must provide: templates/_gateway/GATEWAY-CONTRACT.md
"""

import os
from functools import cache

from agent_framework import MCPStreamableHTTPTool
from agent_framework.openai import OpenAIChatCompletionClient
from azure.identity import DefaultAzureCredential
from azure.identity.aio import DefaultAzureCredential as AsyncCredential
from azure.identity.aio import get_bearer_token_provider
from openai import AsyncOpenAI
from opentelemetry import propagate
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not set; see templates/_gateway/GATEWAY-CONTRACT.md")
    return value


@cache
def _async_credential() -> AsyncCredential:
    # One per process: a credential owns an HTTP session, and hosts build clients per request.
    return AsyncCredential()


@cache
def _sync_credential() -> DefaultAzureCredential:
    return DefaultAzureCredential()


@cache
def _forward_trace_context() -> None:
    # Adds W3C traceparent to every httpx call (the OpenAI client), so the gateway sees one
    # correlation ID per request: agent -> gateway -> model (02 flow 5). Safe to call twice.
    HTTPXClientInstrumentor().instrument()


def chat_client(model: str | None = None) -> OpenAIChatCompletionClient:
    """Chat client for the gateway Models API. `model` is the logical model name, never a deployment."""
    _forward_trace_context()
    token = get_bearer_token_provider(_async_credential(), _env("GATEWAY_TOKEN_SCOPE"))
    return OpenAIChatCompletionClient(
        model=model or _env("GATEWAY_MODEL_NAME"),
        async_client=AsyncOpenAI(base_url=_env("GATEWAY_MODELS_BASE_URL"), api_key=token),
    )


def mcp_tool(name: str, server_path: str, **options) -> MCPStreamableHTTPTool:
    """An MCP server published on the gateway Tools and MCP API (never a server's own URL).

    Approval defaults to always_require. Pass approval_mode="never_require" only for servers the
    MCP registry lists as read-only. Tool credentials stay in the gateway; only our Entra token is sent.
    """
    credential = _sync_credential()
    scope = _env("GATEWAY_TOKEN_SCOPE")
    options.setdefault("approval_mode", "always_require")

    def headers(_: dict) -> dict[str, str]:
        out = {"Authorization": f"Bearer {credential.get_token(scope).token}"}
        propagate.inject(out)  # traceparent; the MCP client does not go through the httpx instrumentation
        return out

    return MCPStreamableHTTPTool(
        name,
        _env("GATEWAY_MCP_BASE_URL").rstrip("/") + "/" + server_path.lstrip("/"),
        header_provider=headers,
        **options,
    )
