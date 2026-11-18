# Hosting shape adapted from microsoft-foundry/foundry-samples (MIT). The room and its guards are original.
"""Foundry-hosted group chat: ResponsesHostServer + Cosmos DB, models through the AI Gateway.

Several agents talk in ONE room and everyone reads the whole transcript, so anything said in the room is
visible to every participant. policy.yaml filters only the shared FACTS channel, not the room. That is why:

  * every participant must have the same clearance level (check_same_clearance, run in the factory BEFORE
    any model call, so it cannot be skipped);
  * keep sensitive values out of the room: let specialists read them from their own policy-filtered facts and
    say only conclusions.

What was found by running this against a real host:
  * max_rounds=None (or omitted) never stops, and 0 is silently raised to 1. build_room requires an int >= 1.
  * without output_from="all" the caller receives only "The group chat has reached the maximum number of
    rounds", not what the participants said.
  * a room that raises while it is being built reaches the caller as a generic failure; the reason is only
    in the server log. Keep the room's shape in code (not in request input) so this cannot be triggered by a caller.
  * a failed turn BLOCKS its conversation for good (the host will not replay possibly-applied effects). Tell the
    user to start a new conversation.
"""
from __future__ import annotations

import logging

from agent_framework import Message, Workflow
from agent_framework.orchestrations import GroupChatBuilder, GroupChatState
from agent_framework_foundry_hosting import (
    HostedResponseRequest,
    ResponsesHostServer,
    WorkflowTurn,
    response_input_messages,
)
from dotenv import load_dotenv

load_dotenv()  # before the app imports: settings read the environment at import time

from app.blank_input import BLANK_INPUT_REPLY  # noqa: E402
from app.diagnostics import configure_logging  # noqa: E402
from app.factory import build_agent  # noqa: E402
from app.memory.checkpoints import CosmosCheckpointStoreProvider  # noqa: E402
from app.memory.fact_store import SharedFactStore, cosmos_client  # noqa: E402
from app.memory.policy import load_policy  # noqa: E402
from app.settings import DEFAULT_TENANT_ID, RequestScope  # noqa: E402
from gateway_client import chat_client  # noqa: E402  (R1: the model only through the AI Gateway)

log = logging.getLogger("hosted.group_chat")

WORKFLOW_NAME = "group_chat"
REPLY_ITEM_TYPES = frozenset({"function_call_output", "mcp_approval_response"})

# ---- EDIT THIS: the room. Name = an agent in config/policy.yaml; the rest is the agent's job. ----
# All participants must share one max_classification in policy.yaml. This sample uses four "internal" agents.
ROOM = [
    ("researcher", "Bring facts only. Keep each turn under 120 words.", "Brings facts and sources."),
    ("analyst", "Weigh trade-offs (cost, ops, risk). Under 120 words.", "Weighs trade-offs."),
    ("reviewer", "Challenge weak claims. Under 80 words.", "Challenges weak claims."),
    ("writer", "When the team agrees, write the final decision in three sentences.", "Writes the decision."),
]
MAX_ROUNDS = 8  # one round = one speaker's turn; must be an int >= 1


def check_same_clearance(names: list[str]) -> None:
    """Refuse a room that mixes clearance levels: a PII-cleared agent must not share a room with a public-only one."""
    levels = {n: load_policy().for_agent(n).max_level for n in names}
    if len(set(levels.values())) != 1:
        raise ValueError(f"Group chat participants have mixed clearance: {levels}")


def build_room(agents: list, *, max_rounds: int) -> Workflow:
    """No checkpoint_storage: the host owns checkpointing and supplies the Cosmos provider."""
    if not isinstance(max_rounds, int) or isinstance(max_rounds, bool) or max_rounds < 1:
        raise ValueError("max_rounds must be an integer >= 1 (None never stops; 0 is silently raised to 1)")
    names = [a.name for a in agents]

    def round_robin(state: GroupChatState) -> str:
        return names[state.current_round % len(names)]

    return GroupChatBuilder(name=WORKFLOW_NAME, participants=agents, selection_func=round_robin,
                            max_rounds=max_rounds, termination_condition=ended_on_blank_input,
                            output_from="all").build()


def ended_on_blank_input(conversation: list[Message]) -> bool:
    """End the room as soon as the blank-input reply has been given, so a blank message does not spend the whole round
    budget on three more agents answering an empty conversation. It looks only at the LAST message, so an earlier
    blank turn in a long conversation does not end later turns.

    It matches the reply's TEXT, not a marker: the framework drops additional_properties when it turns a streamed
    update into a message (seen on the real host), so a `blank_input` flag does not reach this function."""
    if not conversation:
        return False
    last = conversation[-1]
    return last.role == "assistant" and (last.text or "").strip() == BLANK_INPUT_REPLY


def scope_from(request: HostedResponseRequest) -> RequestScope:
    """Tenant from TENANT_ID. User: the host's TRUSTED platform user id, never a caller-supplied field."""
    trusted_user = request.scope.user_id or "local-dev"
    if request.scope.is_hosted and not request.scope.user_id:
        raise RuntimeError("hosted request without a platform user id")
    return RequestScope(DEFAULT_TENANT_ID, trusted_user, request.conversation_id or request.scope.session_id)


def _item_type(item) -> str | None:
    return getattr(item, "type", None) or (item.get("type") if isinstance(item, dict) else None)


async def is_resume(request: HostedResponseRequest) -> bool:
    return any(_item_type(i) in REPLY_ITEM_TYPES for i in await request.get_input_items())


async def is_blank(request: HostedResponseRequest) -> bool:
    items = await request.get_input_items()
    if not items:
        return True
    text = await request.get_input_text()
    return text is not None and not text.strip()


COSMOS = cosmos_client()  # shared across requests (safe: not a MAF agent, client or provider)


async def workflow_factory(request: HostedResponseRequest) -> Workflow:
    """ALWAYS the same graph. A blank message is answered by BlankInputMiddleware on each agent, inside this room:
    a separate "blank" workflow would have a different graph hash and the host would refuse every later message in
    that conversation ("The stored workflow graph is incompatible")."""
    check_same_clearance([name for name, _, _ in ROOM])  # before any model call
    scope = scope_from(request)
    client = chat_client()  # per request
    facts = SharedFactStore.from_client(COSMOS)
    agents = [build_agent(client=client, history=None, facts=facts, name=name, instructions=instructions,
                          scope=scope, description=description, answer_blank_input=True)
              for name, instructions, description in ROOM]
    return build_room(agents, max_rounds=MAX_ROUNDS)


async def parse_turn(request: HostedResponseRequest) -> WorkflowTurn:
    if await is_resume(request):
        return WorkflowTurn(responses=await request.get_workflow_responses())
    if await is_blank(request):
        # A real (empty) user message into the SAME graph; the agents' BlankInputMiddleware answers it.
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
