"""Sequential pipeline: researcher -> analyst -> writer, passing results as policy-filtered FACTS
(a library pattern, not hosted).

Each stage publishes a small result; the next stage reads only the keys policy.yaml allows. The writer never
sees raw research, only the analyst's summary.

What was found by running it against a real model:
  * each stage received exactly the facts policy.yaml lists for it (3 of 3 runs, 0 violations);
  * a blank fact is rejected, and a 5,000-character fact is stored as 4,000, and the next stage receives all 4,000;
  * RE-RUNNING IN THE SAME CONVERSATION CAN SKIP PUBLISHING. A session id is deterministic, so a second run replays
    the first run's history; in a test the researcher and analyst each made one model call, never called
    publish_fact, and the previous run's facts silently stayed. This sample uses a new conversation id per run;
    otherwise clear the old facts first (SharedFactStore.purge_conversation);
  * if a stage fails, stop: a wrapper that swallowed the failure let the next stage run on missing input.

Use this explicit version when the full conversation must not flow down the chain. MAF's SequentialBuilder
passes the whole conversation down by default, which is fine when that is acceptable (see ../../../orchestration).
"""
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app.factory import runtime  # noqa: E402
from app.settings import RequestScope  # noqa: E402


def demo_scope(conversation_id: str) -> RequestScope:
    return RequestScope(tenant_id=os.environ.get("TENANT_ID", "dev"), user_id="demo-user", conversation_id=conversation_id)


async def main() -> None:
    scope = demo_scope(f"conv-pipeline-{uuid.uuid4().hex[:8]}")  # a NEW conversation per run, see the note above
    async with runtime() as rt:
        # Seed the goal once. Every stage is allowed to read task_goal.
        await rt.facts.publish(scope, "orchestrator", "task_goal", "Assess EV charging demand in Pune for 2027")

        stages = [
            ("researcher", "Research the goal. Publish key findings as research_findings (max 150 words)."),
            ("analyst", "Analyse research_findings. Publish a 5-bullet analysis_summary."),
            ("writer", "Write a 1-page executive brief from analysis_summary. Publish it as draft."),
        ]
        for name, instructions in stages:
            agent = rt.agent(name, instructions, scope)
            result = await agent.run("Do your step now.", session=rt.session(name, scope))  # a failure stops the pipeline
            print(f"=== {name} ===\n{result.text}\n")


if __name__ == "__main__":
    asyncio.run(main())
