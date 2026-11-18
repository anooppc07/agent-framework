"""Make failed turns diagnosable.

The Foundry host logs a failed native workflow turn as only
"Native Responses workflow failed during <phase> (<ExceptionType>)", with no traceback, and
then blocks that conversation's workflow lineage for good. Without a traceback of our own,
an intermittent failure can't be explained afterwards (verification H3).

FailureLoggingMiddleware is attached to every agent. It logs the full traceback, the agent,
the hashed scope and the exception chain before re-raising. It never swallows the error,
because the host's blocking is what prevents a refund from being replayed.
"""
from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable

from agent_framework import AgentContext, AgentMiddleware

log = logging.getLogger("agents.failures")


class FailureLoggingMiddleware(AgentMiddleware):
    def __init__(self, agent_name: str, scope_key: str):
        self.agent_name = agent_name
        self.scope_key = scope_key  # hashed, never a raw id

    async def process(self, context: AgentContext, call_next: Callable[[], Awaitable[None]]) -> None:
        started = time.monotonic()
        try:
            await call_next()
        except Exception as exc:
            chain, cur = [], exc
            while cur is not None and len(chain) < 6:
                chain.append(f"{type(cur).__module__}.{type(cur).__name__}: {str(cur)[:300]}")
                cur = cur.__cause__ or cur.__context__
            log.exception(
                "agent run failed agent=%s scope=%s after=%.1fs stream=%s chain=%s",
                self.agent_name, self.scope_key, time.monotonic() - started,
                getattr(context, "stream", None), " <- ".join(chain),
            )
            raise


def configure_logging(level: str = "INFO") -> None:
    """Tracebacks to stdout (Container Apps / Foundry capture it). Call once at startup."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("agent_framework_foundry_hosting").setLevel(logging.INFO)
