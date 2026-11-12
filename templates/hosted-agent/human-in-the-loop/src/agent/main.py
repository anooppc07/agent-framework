# Copyright (c) Microsoft. All rights reserved. (behaviour ported from microsoft-foundry/foundry-samples, MIT,
# langgraph/responses/07-human-in-the-loop; written for Microsoft Agent Framework)
"""Host the review workflow on the Responses protocol. The workflow itself is in review_flow.py."""

from agent_framework_foundry_hosting import CheckpointStoreProvider, ResponsesHostServer, WorkflowTurn
from dotenv import load_dotenv

from review_flow import Decision, Job, ReviewRequest, make_workflow

# The host checkpoints at every pause and refuses to store types that are not listed here. Checkpoints
# are pickled, so this list is a security control: add a type only if it is yours and you mean to store it.
ALLOWED_CHECKPOINT_TYPES = [f"{t.__module__}:{t.__qualname__}" for t in (Job, ReviewRequest, Decision)]


REPLY_ITEM_TYPES = {"function_call_output", "mcp_approval_response"}


async def parse_response(request):
    """Reply to the pending review, or start a new job. The host needs exactly one of the two.

    `get_workflow_responses()` checks each reply against the stored pause (unknown, duplicate, stale and
    partial replies are rejected before any handler runs), then the workflow resumes.
    """
    items = await request.get_input_items()
    if any(isinstance(i, dict) and i.get("type") in REPLY_ITEM_TYPES for i in items):
        return WorkflowTurn(responses=await request.get_workflow_responses())
    text = (await request.get_input_text() or "").strip()
    return WorkflowTurn(input=Job(task=text))


if __name__ == "__main__":
    load_dotenv()
    ResponsesHostServer(
        workflow=make_workflow,
        parse_response=parse_response,
        checkpoint_store_provider=CheckpointStoreProvider(allowed_checkpoint_types=ALLOWED_CHECKPOINT_TYPES),
    ).run()
