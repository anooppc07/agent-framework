# Copyright (c) Microsoft. All rights reserved. (adapted from microsoft-foundry/foundry-samples, MIT)
"""Hosted agent with OpenTelemetry on, calling its model through the AI Gateway.

Upstream (08-observability) builds FoundryChatClient on the project endpoint. Here the model client
comes from gateway_client, so the model call and its trace both go through the gateway (R1, R8).
"""

import asyncio
from typing import Annotated

from agent_framework import Agent, tool
from agent_framework_foundry_hosting import ResponsesHostServer
from dotenv import load_dotenv
from pydantic import Field

from gateway_client import chat_client

load_dotenv()


@tool(approval_mode="never_require")
def get_ticket_status(
    ticket_id: Annotated[str, Field(description="Support ticket id.")],
) -> str:
    """Return the status of a support ticket. Placeholder: replace with a gateway MCP tool (gateway_client.mcp_tool)."""
    return f"Ticket {ticket_id} is open."


async def main() -> None:
    agent = Agent(
        client=chat_client(),
        instructions="You are a support assistant. Keep answers brief. Use tools for ticket data.",
        tools=[get_ticket_status],
        # The host stores history; the model provider must not keep a second copy.
        default_options={"store": False},
    )
    await ResponsesHostServer(agent).run_async()


if __name__ == "__main__":
    asyncio.run(main())
