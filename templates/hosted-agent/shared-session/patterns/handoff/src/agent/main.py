# Adapted from a verified research build (maf-cosmos-patterns hosted/handoff_app.py); hosting shape from
# microsoft-foundry/foundry-samples (MIT).
"""Foundry-hosted handoff agent: ResponsesHostServer + Cosmos DB, models through the AI Gateway.

Triage hands a customer to billing or tech support. The live conversation is shared by every participant
(that is what handoff is). Shared FACTS between agents go through a policy-filtered Cosmos store.

Rules this file follows, each found by running it against a real host:
  * every agent, tool, chat client and context provider is built INSIDE the per-request factory. The host
    rejects objects shared between requests ("Native workflow requests cannot share ...").
  * no builder-level checkpoint_storage. The host owns checkpointing and asks CosmosCheckpointStoreProvider
    for a store scoped to the trusted platform user.
  * the workflow graph must be IDENTICAL on every turn of a conversation: the host stores a hash of it and refuses a
    different one ("The stored workflow graph is incompatible"). So a blank message is answered by a middleware on each
    agent (app/blank_input.py), inside the same workflow, and never by a separate "blank" workflow.
  * a failed turn BLOCKS its conversation for good (the host will not replay a possibly-applied refund).
    Tell the user to start a new conversation. Every agent logs a full traceback (app/diagnostics.py),
    because the host logs only the exception type.
"""
from __future__ import annotations

import logging

from agent_framework import Message, Workflow
from agent_framework_foundry_hosting import (
    HostedResponseRequest,
    ResponsesHostServer,
    WorkflowTurn,
    response_input_messages,
)
from dotenv import load_dotenv

load_dotenv()  # before the app imports: settings read the environment at import time

from app.diagnostics import configure_logging  # noqa: E402
from app.factory import build_agent  # noqa: E402
from app.memory.checkpoints import CosmosCheckpointStoreProvider  # noqa: E402
from app.memory.fact_store import SharedFactStore, cosmos_client  # noqa: E402
from app.settings import DEFAULT_TENANT_ID, RequestScope  # noqa: E402
from gateway_client import chat_client  # noqa: E402  (R1: the model only through the AI Gateway)
from workflows.handoff import WORKFLOW_NAME, build_handoff_workflow  # noqa: E402

log = logging.getLogger("hosted.handoff")


# Input items that answer a paused workflow (tool approval, "waiting for user" request_info).
REPLY_ITEM_TYPES = frozenset({"function_call_output", "mcp_approval_response"})


def scope_from(request: HostedResponseRequest) -> RequestScope:
    """Tenant: one hosted agent per company, from TENANT_ID. User: the host's TRUSTED platform user id,
    never a caller-supplied field. The conversation id may come from the caller: it is always combined
    with the trusted user, so someone else's conversation id gives an empty, different partition."""
    trusted_user = request.scope.user_id or "local-dev"
    if request.scope.is_hosted and not request.scope.user_id:
        raise RuntimeError("hosted request without a platform user id")
    conversation = request.conversation_id or request.scope.session_id
    return RequestScope(DEFAULT_TENANT_ID, trusted_user, conversation)


def _item_type(item) -> str | None:
    return getattr(item, "type", None) or (item.get("type") if isinstance(item, dict) else None)


async def is_resume(request: HostedResponseRequest) -> bool:
    return any(_item_type(i) in REPLY_ITEM_TYPES for i in await request.get_input_items())


async def is_blank(request: HostedResponseRequest) -> bool:
    items = await request.get_input_items()
    if not items:
        return True
    text = await request.get_input_text()
    # Resume turns (approvals, function_call_output) carry items but no text: not blank.
    return text is not None and not text.strip()


# Shared across requests (safe: not MAF agents, clients or providers): the Cosmos SDK client.
COSMOS = cosmos_client()


async def workflow_factory(request: HostedResponseRequest) -> Workflow:
    """ALWAYS the same graph. A blank message is answered by BlankInputMiddleware on each agent, inside this workflow:
    a separate "blank" workflow would have a different graph hash and the host would refuse every later message in
    that conversation ("The stored workflow graph is incompatible")."""
    scope = scope_from(request)
    client = chat_client()  # per request
    facts = SharedFactStore.from_client(COSMOS)

    def make_agent(name, instructions, description, tools=None):
        return build_agent(client=client, history=None, facts=facts, name=name,
                           instructions=instructions, scope=scope, description=description,
                           tools=tools, handoff_participant=True, answer_blank_input=True)

    return build_handoff_workflow(make_agent)  # no checkpoint_storage when hosted


async def parse_turn(request: HostedResponseRequest) -> WorkflowTurn:
    """Map one Responses turn to a workflow start or a batch of replies to a pause.

    Replies are validated by the host against this user's scoped checkpoint before any tool runs, so a
    caller cannot answer someone else's pending approval."""
    if await is_resume(request):
        return WorkflowTurn(responses=await request.get_workflow_responses())
    if await is_blank(request):
        # A real (empty) user message into the SAME graph; the agent's BlankInputMiddleware answers it.
        return WorkflowTurn(input=[Message("user", [" "])])
    return WorkflowTurn(input=await response_input_messages(request))


def build_server() -> ResponsesHostServer:
    return ResponsesHostServer(
        workflow=workflow_factory,
        parse_response=parse_turn,
        checkpoint_store_provider=CosmosCheckpointStoreProvider(COSMOS),
    )


if __name__ == "__main__":
    configure_logging()
    build_server().run()
