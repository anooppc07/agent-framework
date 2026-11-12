"""Foundry hosted-agent entrypoint using the standard MAF Responses host.

Sessions and conversation history are handled by the hosting integration: leave the host's
optional storage-provider arguments unset and it persists MAF sessions in Foundry-managed
storage. Do NOT pass any storage override here; `preflight.py` fails if you do. No protocol,
streaming or session code belongs in this file.
"""

import os

from agent_framework_foundry_hosting import ResponsesHostServer
from dotenv import load_dotenv

from agent import build_agent


def build_server(agent):
    return ResponsesHostServer(agent=agent, history_source="agent_server")


if __name__ == "__main__":
    load_dotenv()
    build_server(build_agent()).run()
