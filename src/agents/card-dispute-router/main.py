"""Foundry hosted-agent entrypoint for the router: the standard MAF Responses host, native workflow.

The workflow is hosted natively with `workflow=` and a per-request factory, which is the supported
shape (wrapping a workflow as an agent is deprecated because it shares state across requests). Leave
the host's storage-provider arguments unset so it uses Foundry-managed sessions and checkpoints;
`preflight.py` fails if you override them. No protocol, streaming or storage code belongs here.
"""

import json
import os
from pathlib import Path

from agent_framework import Message
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer, WorkflowTurn
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

from agent import build_workflow

_CONFIG = json.loads((Path(__file__).parent / "specialists.json").read_text(encoding="utf-8"))
_DEPLOYMENT = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-5-mini")


def _client(role):
    # One client and credential per role, created inside the request-scoped factory.
    return FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=_DEPLOYMENT,
        credential=DefaultAzureCredential(),
    )


def make_workflow(request):
    """Build a fresh workflow, executors, agents and clients for this request."""
    return build_workflow(_CONFIG["specialists"], _client, router_instructions=_CONFIG["router_instructions"])


async def parse_response(request):
    """Hand the router this turn's input as messages; the host owns history and checkpoints."""
    text = await request.get_input_text() or ""
    return WorkflowTurn(input=[Message("user", [text])])


def build_server():
    return ResponsesHostServer(workflow=make_workflow, parse_response=parse_response)


if __name__ == "__main__":
    load_dotenv()
    build_server().run()
