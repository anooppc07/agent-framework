"""{{AGENT_NAME}}: Microsoft Agent Framework (MAF) agent for Foundry hosted agents.

Conversation history and sessions are owned by the Foundry hosting integration (see main.py);
this module holds only application behaviour: instructions, tools and a small `run_turn`
facade used by tests and evals. It is not an invocation protocol.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from agent_framework import Agent, Message, tool

_DEPLOYMENT = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "{{MODEL}}")
_INSTRUCTIONS = (Path(__file__).parent / "instructions.md").read_text(encoding="utf-8")

REFUSAL = "I can't help with an empty request. Please tell me what you need."


# --- sample-tool begin: replace with real tools; for tools=none keep `_tools = []` ---
@tool
def echo(text: str) -> str:
    """Echo the text back. Placeholder tool; replace with a real one."""
    return text


_tools = [echo]
# --- sample-tool end ---


@dataclass(frozen=True)
class TurnResult:
    answer: str
    outcome: str  # "answered" | "refused"


def _foundry_client():
    # Imported lazily so tests and importers need no Azure configuration.
    from agent_framework.foundry import FoundryChatClient
    from azure.identity import DefaultAzureCredential

    return FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=_DEPLOYMENT,
        credential=DefaultAzureCredential(),
    )


def build_agent(client=None) -> Agent:
    """Build the agent. Stateless: no session, history or checkpoint is kept here."""
    return Agent(client=client or _foundry_client(), instructions=_INSTRUCTIONS, tools=_tools)


async def run_turn(agent: Agent, user_input: str, history=()) -> TurnResult:
    """Run one turn. `history` is prior (role, text) pairs supplied by the caller/host.

    Errors from the model propagate; nothing is retried or fabricated.
    """
    if not user_input or not user_input.strip():
        return TurnResult(REFUSAL, "refused")
    messages = [Message(role, [text]) for role, text in history] + [Message("user", [user_input])]
    response = await agent.run(messages)
    return TurnResult(response.text, "answered")
