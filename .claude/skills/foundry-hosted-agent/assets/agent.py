"""{{AGENT_NAME}}: LangGraph ReAct agent configured for Foundry hosted agents."""

import os
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from dotenv import load_dotenv
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

load_dotenv()

_SCOPE = "https://ai.azure.com/.default"
_DEPLOYMENT = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "{{MODEL}}")
_INSTRUCTIONS = (Path(__file__).parent / "instructions.md").read_text(encoding="utf-8")


# --- sample-tool begin: replace with real tools; for tools=none keep `_tools = []` ---
@tool
def echo(text: str) -> str:
    """Echo the text back. Placeholder tool; replace with a real one."""
    return text


_tools = [echo]
# --- sample-tool end ---


def _get_llm() -> ChatOpenAI:
    # FOUNDRY_PROJECT_ENDPOINT is injected by the platform (hosted) / azd (local).
    credential = DefaultAzureCredential()
    project = AIProjectClient(endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"].rstrip("/"), credential=credential)
    return ChatOpenAI(
        model=_DEPLOYMENT,
        base_url=str(project.get_openai_client().base_url),
        api_key=get_bearer_token_provider(credential, _SCOPE),
    )


_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = create_react_agent(_get_llm(), _tools, prompt=_INSTRUCTIONS)
    return _graph
