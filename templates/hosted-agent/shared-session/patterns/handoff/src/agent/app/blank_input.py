"""Answer a blank user message without calling the model, and without changing the workflow.

Why this is a middleware on every agent and not a separate "blank" workflow: the host stores a hash of the workflow's
graph with each conversation and refuses to continue a conversation with a different graph ("The stored workflow graph is
incompatible"). A separate blank-input workflow under the same name has a different graph, so a blank FIRST message made the
user's next real message fail for good, and a blank message in the middle of a conversation failed outright. Keeping the
graph identical for every turn avoids that.

Fires only when the LAST message is a user message with no text. In a handoff an agent also runs between user turns
(after a handoff, with the previous agent's tool result as the last message), and that must never be answered with the
blank-input reply.

The hosted workflow runs its agents in STREAMING mode even for a request sent with stream=false (seen on the real host), so
the short-circuit has two shapes: an AgentResponse for a plain run, and a one-update ResponseStream for a streaming run.
"""
from __future__ import annotations

from typing import Awaitable, Callable

from agent_framework import (
    AgentContext,
    AgentMiddleware,
    AgentResponse,
    AgentResponseUpdate,
    Content,
    Message,
    ResponseStream,
)

BLANK_INPUT_REPLY = "I didn't receive a message. Please type your question."


def is_blank_user_turn(messages: list[Message]) -> bool:
    """True only for a user message made of TEXT that is empty or whitespace.

    A message with ANY other content is not blank, even though its .text is empty. The reply to a tool approval is a user
    message with no text and a `function_approval_response` content item; treating that as blank swallowed the approval, so
    the refund never ran (found by running the full handoff flow, not by the blank-input tests)."""
    if not messages:
        return False
    last = messages[-1]
    if last.role != "user":
        return False
    if not last.contents:
        return True  # a user message with no content at all
    only_text = all(c.type == "text" for c in last.contents)
    return only_text and not (last.text or "").strip()


def _reply(agent_name: str | None) -> Message:
    return Message("assistant", [BLANK_INPUT_REPLY], additional_properties={"blank_input": True}, author_name=agent_name)


def _stream(agent_name: str | None) -> ResponseStream:
    async def updates():
        yield AgentResponseUpdate(contents=[Content.from_text(text=BLANK_INPUT_REPLY)], role="assistant",
                                  author_name=agent_name, additional_properties={"blank_input": True})

    return ResponseStream(updates(), finalizer=AgentResponse.from_updates)


class BlankInputMiddleware(AgentMiddleware):
    async def process(self, context: AgentContext, call_next: Callable[[], Awaitable[None]]) -> None:
        if is_blank_user_turn(context.messages):
            name = getattr(context.agent, "name", None)
            context.result = _stream(name) if context.stream else AgentResponse(messages=[_reply(name)])
            return  # the model is not called
        await call_next()
