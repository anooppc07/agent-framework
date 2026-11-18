"""Single agent with durable private memory (a library pattern, not hosted).

One agent, one private history in Cosmos. Run this twice, or restart the process: the agent still remembers,
because the history key is derived from tenant + user + conversation + agent (a hash, never a raw id).

Why this is NOT a hosted template: a hosted regular agent cannot tell its callers apart. The host builds the
agent once, so it has one fixed RequestScope, and no tool or provider receives the trusted user. In a test, a
second user who sent the first user's conversation id was shown the first user's facts and messages. A hosted
version has to be a workflow, whose factory does receive the request (see ../handoff/src/agent/main.py).
Use this pattern in a script, a job, or your own API, and build the RequestScope per request from the caller's
validated identity.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv  # noqa: E402

load_dotenv()  # before the app imports: settings read the environment at import time

from app.factory import runtime  # noqa: E402
from app.settings import RequestScope  # noqa: E402


def demo_scope(conversation_id: str) -> RequestScope:
    # In an API, build this from the caller's validated Entra token: tenant and user from claims, never from the body.
    return RequestScope(tenant_id=os.environ.get("TENANT_ID", "dev"), user_id="demo-user", conversation_id=conversation_id)


async def ask(agent, session, text: str) -> str:
    if not text or not text.strip():
        raise ValueError("message must not be blank")  # the library stores a blank turn and still calls the model
    return (await agent.run(text, session=session)).text


async def main() -> None:
    scope = demo_scope("conv-single")
    async with runtime() as rt:
        assistant = rt.agent(
            "assistant",
            "You are a helpful assistant. Save stable user preferences with publish_fact.",
            scope,
        )
        session = rt.session("assistant", scope)
        print(await ask(assistant, session, "Hi, I prefer answers in bullet points."))
        print(await ask(assistant, session, "What format do I prefer?"))


if __name__ == "__main__":
    asyncio.run(main())
