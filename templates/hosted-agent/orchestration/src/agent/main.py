# Copyright (c) Microsoft. All rights reserved. (hosting shape adapted from microsoft-foundry/foundry-samples, MIT)
"""Host an orchestration (sequential chain or group chat) on the Responses protocol.

The host uses its default Foundry-managed stores for sessions and checkpoints. Do not override
`agent_session_store_provider` or `checkpoint_store_provider` here: preflight fails the build if you do, and
templates/hosted-agent/custom-store is the sanctioned place for a store the PortCo owns.
"""
import os

from agent_framework_foundry_hosting import ResponsesHostServer, WorkflowTurn
from dotenv import load_dotenv

from orchestration_flow import build_group_chat, build_sequential

PATTERN = os.environ.get("ORCHESTRATION_PATTERN", "sequential")  # "sequential" | "group-chat"
MAX_ROUNDS = 4  # drafter, reviewer, drafter, reviewer


async def parse_response(request) -> WorkflowTurn:
    """Start a turn from the user's text. A blank request is answered by the workflow's input gate.

    Raising here would reach the caller as a generic server_error, indistinguishable from an outage.
    """
    return WorkflowTurn(input=await request.get_input_text() or " ")


def make_workflow(request):
    """Fresh workflow per request with stable executor ids, as the host requires. Models: gateway only (R1)."""
    from gateway_client import chat_client  # imported lazily so tests need no gateway environment
    from agent_framework import Agent

    def agent(name: str, role: str) -> Agent:
        return Agent(client=chat_client(), name=name, instructions=role)

    if PATTERN == "group-chat":
        # In a group chat every agent sees the whole conversation, so overlapping instructions make them
        # repeat each other (seen against a real model: three identical turns out of six). Give each a
        # different job and tell them not to restate earlier messages.
        drafter = agent("drafter", "You write the draft. On your first turn write one short draft answering the "
                        "user. On later turns apply the reviewer's feedback and reply with the revised draft only. "
                        "Never repeat a draft you already wrote.")
        reviewer = agent("reviewer", "You give feedback and never rewrite the draft. Reply with one sentence naming "
                         "one concrete change to make. Do not repeat earlier feedback.")
        order = ["drafter", "reviewer"]
        return build_group_chat([drafter, reviewer], max_rounds=MAX_ROUNDS,
                                selection_func=lambda s: order[s.current_round % len(order)])
    drafter = agent("drafter", "Draft a clear answer to the user's request.")
    reviewer = agent("reviewer", "Review the draft above. Reply with the improved final text only.")
    return build_sequential([drafter, reviewer])


def build_server() -> ResponsesHostServer:
    return ResponsesHostServer(workflow=make_workflow, parse_response=parse_response)


if __name__ == "__main__":
    load_dotenv()
    build_server().run()
