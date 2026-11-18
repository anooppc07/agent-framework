"""Orchestration template: sequential chain and group chat, one AgentSession per executor.

Each participant is wrapped by the builder in its own AgentExecutor with its own AgentSession. The
orchestrator forwards the conversation, so no session is shared (a shared session duplicates context and
is lost on checkpoint restore; see docs/handoff/HANDOFF.md). Build a fresh workflow per request and keep
executor ids stable so a checkpoint can be restored into it.
"""
from collections.abc import Callable, Sequence

from agent_framework import Agent, CheckpointStorage, Executor, Message, Workflow, WorkflowContext, handler
from agent_framework.orchestrations import GroupChatBuilder, SequentialBuilder

# What the caller receives for blank input. The host turns an exception into a generic server_error that a
# caller cannot tell from an outage, so a rejection is a normal, clearly marked result (same idea as the
# human-in-the-loop template).
REJECTED = "REJECTED: input must be a non-empty string"
GATE_ID = "input-gate"


def validate_input(text: str | None) -> str:
    """Return the stripped user text, or raise ValueError for None, empty or whitespace-only input.

    MAF accepts "" and runs the model on an empty user message, so the host must reject it first.
    """
    if text is None or not text.strip():
        raise ValueError("input must be a non-empty string")
    return text.strip()


class InputGate(Executor):
    """First participant of a sequential chain: blank input ends the run with REJECTED and no model call."""

    def __init__(self) -> None:
        super().__init__(id=GATE_ID)

    @handler
    async def check(self, messages: list[Message], ctx: WorkflowContext[list[Message], str]) -> None:
        try:
            validate_input(" ".join(m.text or "" for m in messages))
        except ValueError:
            await ctx.yield_output(REJECTED)
            return
        await ctx.send_message(messages)


def is_blank_conversation(conversation: Sequence[Message]) -> bool:
    return not any((m.text or "").strip() for m in conversation)


def build_sequential(agents: Sequence[Agent], *, name: str = "sequential-v1",
                     checkpoint_storage: CheckpointStorage | None = None) -> Workflow:
    """The gate is a participant, not a wrapper: nesting the chain in an outer workflow makes a resume after
    a failure re-run every earlier agent (measured: resume calls [a, b] instead of [b])."""
    if not agents:
        raise ValueError("at least one agent is required")
    gate = InputGate()
    return SequentialBuilder(name=name, participants=[gate, *agents], checkpoint_storage=checkpoint_storage,
                             output_from=[gate, agents[-1].name]).build()


def build_group_chat(agents: Sequence[Agent], *, selection_func: Callable, max_rounds: int,
                     name: str = "group-chat-v1",
                     checkpoint_storage: CheckpointStorage | None = None) -> Workflow:
    """max_rounds is required: MAF treats None as unbounded and silently raises 0 to 1.

    output_from="all" surfaces each participant's reply to the caller; without it the caller receives only
    MAF's terminal line. A blank opener ends the chat with no model call through the termination condition;
    GroupChatBuilder has no slot for a gate, so the caller sees MAF's termination line, not REJECTED.
    """
    if max_rounds is None or max_rounds < 1:
        raise ValueError("max_rounds must be an integer >= 1")
    return GroupChatBuilder(name=name, participants=list(agents), selection_func=selection_func,
                            max_rounds=max_rounds, termination_condition=is_blank_conversation,
                            output_from="all", checkpoint_storage=checkpoint_storage).build()
