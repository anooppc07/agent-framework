"""Foundry hosted-agent entrypoint: serves the graph over the Responses protocol.

Conversation state is kept server-side by the platform via previous_response_id.
"""

import os

from langchain_azure_ai.agents.hosting import ResponsesHostServer

from agent import get_graph

if __name__ == "__main__":
    ResponsesHostServer(get_graph()).run(port=int(os.environ.get("PORT", "8088")))
