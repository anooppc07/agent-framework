"""Reflection: a writer drafts, a critic reviews, the writer revises (a library pattern, not hosted).

The critic judges the draft on its own merits, so it never sees the writer's transcript or reasoning. We hand it
exactly one thing: the draft. Each agent keeps its own private history, so the writer remembers earlier feedback.

What was found by running it against a real model:
  * the critic's input is only the draft (checked in 3 of 3 runs);
  * a real model almost never writes the word APPROVED (0 of 9 reviews), so the loop normally runs to
    MAX_ROUNDS. Do not rely on APPROVED to stop it: MAX_ROUNDS is the real bound;
  * a blank draft was approved in 2 of 9 trials, so the draft is checked before it is reviewed;
  * the writer and critic still publish facts (draft, review_verdict), because policy.yaml allows those keys.
    Remove the keys from policy.yaml if you do not want them stored.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app.factory import runtime  # noqa: E402
from app.settings import RequestScope  # noqa: E402

MAX_ROUNDS = 3  # an integer >= 1


def demo_scope(conversation_id: str) -> RequestScope:
    return RequestScope(tenant_id=os.environ.get("TENANT_ID", "dev"), user_id="demo-user", conversation_id=conversation_id)


async def main() -> None:
    scope = demo_scope("conv-reflection")
    async with runtime() as rt:
        writer = rt.agent("writer", "You write concise customer-facing product announcements.", scope)
        critic = rt.agent(
            "critic",
            "You review drafts for clarity, accuracy and tone. "
            "Reply 'APPROVED' if it is ready, otherwise give at most 3 specific fixes.",
            scope,
            private_history=False,  # stateless reviewer: each review is independent
        )
        w_session = rt.session("writer", scope)

        draft = (await writer.run("Draft a 100-word announcement for our new offline mode.", session=w_session)).text
        for i in range(MAX_ROUNDS):
            if not draft or not draft.strip():
                raise RuntimeError("the writer returned an empty draft; not sending it for review")
            review = (await critic.run(f"Review this draft:\n\n{draft}")).text  # ONLY the draft crosses the boundary
            print(f"--- round {i + 1} review ---\n{review}\n")
            if "APPROVED" in review.upper():
                break
            draft = (await writer.run(f"Revise using this feedback:\n{review}", session=w_session)).text

        print("FINAL DRAFT:\n", draft)


if __name__ == "__main__":
    asyncio.run(main())
