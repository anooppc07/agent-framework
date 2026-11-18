"""One place to build agents so every agent gets the same wiring:

  * private history in Cosmos (own hashed partition per tenant|user|conversation|agent)
  * policy-filtered shared facts + a publish_fact tool
  * checkpoints confined to the request scope
  * models only through the AI Gateway (gateway_client), Entra ID auth, no keys, no create calls

For self-run scripts and your own APIs (the library samples). A hosted workflow uses
build_agent() inside its per-request factory instead (see handoff/ and group-chat/).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass

from agent_framework import Agent, AgentSession
from agent_framework.azure import CosmosHistoryProvider
from azure.cosmos.aio import CosmosClient

from app.blank_input import BlankInputMiddleware
from app.diagnostics import FailureLoggingMiddleware
from app.memory.checkpoints import ScopedCheckpointStorage, scoped_checkpoints
from app.memory.fact_store import FactStore, SharedFactStore, cosmos_client
from app.memory.scoped_context import ScopedFactsProvider
from app.settings import SETTINGS, RequestScope
from gateway_client import chat_client  # R1: the model is reached only through the AI Gateway


def history_provider(client: CosmosClient) -> CosmosHistoryProvider:
    """Bound to the pre-provisioned container (pk /session_id, TTL from infra/cosmos)."""
    container = client.get_database_client(SETTINGS.cosmos_db).get_container_client(
        SETTINGS.history_container)
    return CosmosHistoryProvider(container_client=container)


def build_agent(
    *,
    client,
    history: CosmosHistoryProvider | None,
    facts: FactStore | None,
    name: str,
    instructions: str,
    scope: RequestScope,
    description: str = "",
    tools: list | None = None,
    handoff_participant: bool = False,
    answer_blank_input: bool = False,
) -> Agent:
    """answer_blank_input=True (the hosted templates) makes a blank user message get a friendly reply with no model call,
    inside the SAME workflow. See app/blank_input.py for why it is not a separate workflow."""
    providers = []
    if history is not None:
        providers.append(history)
    if facts is not None:
        providers.append(ScopedFactsProvider(name, scope, facts))
    middleware = [FailureLoggingMiddleware(name, scope.key)]
    if answer_blank_input:
        middleware.insert(0, BlankInputMiddleware())  # outermost: a blank turn never reaches the model or the facts store
    return Agent(
        client=client,
        name=name,
        description=description or instructions[:120],
        instructions=instructions,
        tools=tools or [],
        context_providers=providers,
        middleware=middleware,
        # HandoffBuilder refuses to build without this (keeps local history
        # consistent when a handoff tool call short-circuits the run).
        require_per_service_call_history_persistence=handoff_participant,
    )


@dataclass
class Runtime:
    chat_client: object
    history: CosmosHistoryProvider
    facts: FactStore
    cosmos: CosmosClient

    def agent(
        self,
        name: str,
        instructions: str,
        scope: RequestScope,
        *,
        description: str = "",
        tools: list | None = None,
        private_history: bool = True,
        shared_facts: bool = True,
        handoff_participant: bool = False,
    ) -> Agent:
        return build_agent(
            client=self.chat_client,
            history=self.history if private_history else None,
            facts=self.facts if shared_facts else None,
            name=name, instructions=instructions, scope=scope, description=description,
            tools=tools, handoff_participant=handoff_participant,
        )

    def checkpoints_for(self, scope: RequestScope) -> ScopedCheckpointStorage:
        """Checkpoint store confined to this tenant + user + conversation."""
        return scoped_checkpoints(self.cosmos, scope)

    @staticmethod
    def session(name: str, scope: RequestScope) -> AgentSession:
        """Deterministic private session id: resumes after a restart, never shared between agents."""
        return AgentSession(session_id=scope.private_session_id(name))


@asynccontextmanager
async def runtime():
    """Direct Cosmos access for agents inside one app. Private history and checkpoints stay in this
    service's own Cosmos account."""
    client = cosmos_client()
    try:
        yield Runtime(chat_client=chat_client(), history=history_provider(client),
                      facts=SharedFactStore.from_client(client), cosmos=client)
    finally:
        await client.close()
