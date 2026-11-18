"""Hand-written fake model boundary (no mocks, no network). Records every call it receives."""
import asyncio

from agent_framework import (Agent, BaseChatClient, ChatResponse, ChatResponseUpdate, Content,
                             InMemoryHistoryProvider, Message, ResponseStream)
from agent_framework._tools import FunctionInvocationLayer


class Models:
    def __init__(self, answers=None, errors=None, delays=None):
        self.answers = answers or {}
        self.errors = errors or {}   # role -> exception raised on that role's call
        self.delays = delays or {}   # role -> seconds to wait inside the call (forces interleaving)
        self.calls = []              # (role, [(msg_role, text)])

    def client(self, role):
        outer = self

        class Client(FunctionInvocationLayer, BaseChatClient):
            def _inner_get_response(self, *, messages, stream, options, **kw):
                async def reply():
                    outer.calls.append((role, [(m.role, m.text or "") for m in messages]))
                    await asyncio.sleep(outer.delays.get(role, 0))
                    if outer.errors.get(role) is not None:
                        raise outer.errors[role]
                    return outer.answers.get(role) or f"{role}-reply"

                if not stream:  # workflow.run() is non-streaming
                    async def go():
                        return ChatResponse(messages=Message("assistant", [await reply()]))
                    return go()

                async def updates():  # the hosted server runs executors in streaming mode
                    yield ChatResponseUpdate(role="assistant", contents=[Content.from_text(await reply())])

                return ResponseStream(updates(), finalizer=ChatResponse.from_updates)

        return Client()

    def agent(self, role):
        return Agent(client=self.client(role), instructions=f"You are {role}.", name=role,
                     context_providers=[InMemoryHistoryProvider()])

    def roles(self):
        return [c[0] for c in self.calls]

    def texts_seen_by(self, role):
        return [[t for _, t in c[1]] for c in self.calls if c[0] == role]
