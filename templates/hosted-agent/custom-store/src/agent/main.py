# Copyright (c) Microsoft. All rights reserved. (adapted from microsoft-foundry/foundry-samples, MIT,
# agent-framework/responses/26-custom-store)
"""Host an Agent Framework agent with four custom Redis-backed stores, calling its model through the AI Gateway.

Upstream builds FoundryChatClient on the project endpoint. Here the model client comes from gateway_client (R1).
The four stores are the part you keep: responses, agent sessions, workflow checkpoints, function approvals.
"""

from agent_framework import Agent
from agent_framework_foundry_hosting import ResponsesHostServer
from dotenv import load_dotenv

from gateway_client import chat_client
from redis_database import create_database
from redis_response_store import create_response_store
from redis_state_stores import (
    RedisAgentSessionStoreProvider,
    RedisCheckpointStoreProvider,
    RedisFunctionApprovalStoreProvider,
)
from store_validation_tools import create_store_validation_tools

INSTRUCTIONS = (
    "You are a service operations assistant. Help operators triage and resolve incidents. Keep answers "
    "brief and actionable. When asked to validate a custom store, call the named validation tool exactly "
    "once and include its result verbatim in your response."
)


def main() -> None:
    database = create_database()  # one Redis instance behind all four stores
    agent = Agent(
        client=chat_client(),
        instructions=INSTRUCTIONS,
        tools=create_store_validation_tools(database),
        # The host sends the full transcript each turn; the model provider must not keep a second copy.
        default_options={"store": False},
    )
    ResponsesHostServer(
        agent,
        store=create_response_store(database),
        agent_session_store_provider=RedisAgentSessionStoreProvider(database),
        checkpoint_store_provider=RedisCheckpointStoreProvider(database),
        function_approval_store_provider=RedisFunctionApprovalStoreProvider(database),
    ).run()


if __name__ == "__main__":
    load_dotenv()
    main()
