"""Customer-support handoff workflow (triage -> billing | tech_support).

Hosted by main.py behind the Foundry Responses host.

IMPORTANT: the live handoff conversation is shared by every participant.
policy.yaml governs the shared FACTS channel only. Anything said in the conversation
(the user's raw messages, billing's replies, the refund reason as text) is visible to
tech_support too. If billing details must stay out of the technical agent's context,
handoff is the wrong pattern for that pair: put them in separate services.

Build everything here, per call. The hosting layer rejects agents, clients, tools or
providers reused across requests ("Native workflow requests cannot share ...").
"""
from __future__ import annotations

from typing import Callable

from agent_framework import Agent, CheckpointStorage, Workflow, tool
from agent_framework.orchestrations import HandoffBuilder

WORKFLOW_NAME = "customer_support"

# make_agent(name, instructions, description, tools) -> Agent with handoff_participant=True
AgentMaker = Callable[..., Agent]


def _refund_tool():
    @tool(approval_mode="always_require")
    def submit_refund(order_id: str, amount: str, reason: str) -> str:
        """Submit a refund request (human approval required)."""
        # Replace with your payments API call. Must be idempotent on (order_id, amount):
        # approval + resume guarantees once-per-checkpoint, not once-per-world.
        return f"Refund queued for {order_id}: {amount}"

    return submit_refund


def build_handoff_workflow(
    make_agent: AgentMaker,
    *,
    checkpoint_storage: CheckpointStorage | None = None,  # None when hosted (F7)
) -> Workflow:
    triage = make_agent(
        "triage",
        "Classify the request. Publish customer_intent and order_id. "
        "Hand off billing issues to billing and device issues to tech_support. "
        "Never repeat card numbers or national ids back to the user.",
        "Front door. Routes customers.",
    )
    billing = make_agent(
        "billing",
        "Handle refunds and charges. Publish refund_reason and refund_status.",
        "Refunds, invoices, charges.",
        [_refund_tool()],
    )
    tech = make_agent(
        "tech_support",
        "Fix device and app problems. Publish device_model and resolution.",
        "Device and app troubleshooting.",
    )
    return (
        HandoffBuilder(name=WORKFLOW_NAME, participants=[triage, billing, tech],
                       checkpoint_storage=checkpoint_storage)
        .with_start_agent(triage)
        .add_handoff(triage, [billing, tech])
        .add_handoff(billing, [triage])
        .add_handoff(tech, [triage])
        .build()
    )
