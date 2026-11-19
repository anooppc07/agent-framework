# sequential-pipeline: stages that pass facts, not conversation

A researcher, an analyst and a writer run in order. Each publishes a small result as a **fact**, and the next stage reads only the
facts `policy.yaml` lists for it. Read the [parent README](../../README.md) first.

```
researcher -> publishes research_findings
analyst    -> reads research_findings, publishes analysis_summary
writer     -> reads analysis_summary only, publishes draft      (it never sees the raw research)
```

## Use it when / do not use it when

Use it when the full conversation must **not** flow down the chain, so a later stage sees only a small result from the one before.

Do not use it if passing the whole conversation is fine. MAF's `SequentialBuilder` does that by default; the repo's
[`orchestration`](../../../orchestration) template covers it.

## How it works

`main.py` seeds `task_goal` once, then runs each stage in order with `agent.run("Do your step now.")`. A stage that fails stops the
pipeline. The stages are the `stages` list in `main()`: edit the names and instructions there, and make each name an agent in
`config/policy.yaml`.

## What the tests found, and what the sample does about it

| Finding | What the sample does |
|---|---|
| Each stage received exactly the facts `policy.yaml` lists for it (3 of 3 runs, 0 violations) | By design |
| A blank fact is rejected; a 5,000-character fact is stored as 4,000 and the next stage receives all 4,000 | By design. A 4,000-character fact adds about 4,400 characters to every call of the agent that reads it |
| **A re-run in the same conversation can skip publishing.** Sessions are deterministic, so a second run replays the first run's history; in a test two stages made one model call each, never called `publish_fact`, and the old facts silently stayed | A **new conversation id per run** (`uuid`). Otherwise clear the old facts first with `SharedFactStore.purge_conversation` |
| A wrapper that swallowed a stage failure let the next stage run on missing input | A failure stops the pipeline: it is not caught |

## Run it

Set up Cosmos and your environment as in [Run a pattern locally](../../README.md#run-single-agent-reflection-sequential-pipeline-or-agent-as-tool-locally). Then:

```bash
cd patterns/sequential-pipeline
python main.py
```

## Identity

The four identities are in the [parent README](../../README.md#identity-who-is-who). What differs here: `demo_scope()` hard-codes
`demo-user`; **replace it** with a scope built per request from the caller's validated Entra token. The scope is hashed into the
facts partition, so a second user with the same conversation id reads nothing (tested).

All three stages run under the **same** identity. Their separation is the `policy.yaml` entry for each stage name, which controls
which facts it can read and write, **not** a separate identity or role. The seed `task_goal` is published as `orchestrator`: the
caller of `publish` names the agent, so this check protects against a mistake, not against code that chooses to use another name.

## Status

**Run** (real Cosmos DB, real `gpt-5-mini`): the sample ran as shipped. All four facts (`task_goal`, `research_findings`,
`analysis_summary`, `draft`) were stored with their owners and no raw id was in any container. In the earlier tests a second user
read nothing, a fact was readable by the next stage immediately (0 stale reads in 5 tries), and a failing stage 2 stopped the
loop and a clean re-run left no duplicate keys.

**Not run:** a hosted version, a process killed mid-call, whether the model quality is good (the researcher's reply was just "Done."
because its work went into a fact; the writer's brief read well).
