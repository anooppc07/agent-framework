# Sample: sequential pipeline (through facts)

A researcher, an analyst and a writer run in order. Each publishes a small result as a **fact**, and the next stage reads only the
facts `policy.yaml` lists for it. The writer never sees the raw research, only the analyst's summary. **This is a library sample,
not a hosted template.** Read the [parent README](../../README.md) first.

Use this explicit version when the full conversation must not flow down the chain. MAF's `SequentialBuilder` passes the whole
conversation down by default, which is fine when that is acceptable; the repo's
[`orchestration`](../../../orchestration) template covers that.

## What the tests found, and what the sample does about it

| Finding | What the sample does |
|---|---|
| Each stage received exactly the facts `policy.yaml` lists for it (3 of 3 runs, 0 violations) | By design |
| A blank fact is rejected; a 5,000-character fact is stored as 4,000 and the next stage receives all 4,000 | By design. A 4,000-character fact adds about 4,400 characters to every call of the agent that reads it |
| **A re-run in the same conversation can skip publishing.** Sessions are deterministic, so a second run replays the first run's history; in a test two stages made one model call each, never called `publish_fact`, and the old facts silently stayed | A **new conversation id per run** (`uuid`). Otherwise clear the old facts first with `SharedFactStore.purge_conversation` |
| A wrapper that swallowed a stage failure let the next stage run on missing input | A failure stops the pipeline: it is not caught |

## Identity in this sample

| # | Identity | How it is handled here |
|---|---|---|
| 1 | **The end user** | `demo_scope()` hard-codes `demo-user`. **Replace it** with a scope built per request from the caller's validated Entra token. The scope is hashed into the facts partition, so a second user with the same conversation id reads nothing (tested). |
| 2 | **The identity that runs the sample** | `az login` locally (`APP_ENV=dev`); the managed identity when deployed. It needs *Cosmos DB Built-in Data Contributor* on the database. |
| 3 | **Model access** | `gateway_client.chat_client()` with an Entra token for `GATEWAY_TOKEN_SCOPE`. |
| 4 | **The tenant** | `TENANT_ID`. |

All three stages run under the **same** identity. Their separation is the `policy.yaml` entry for each stage name, which controls
which facts it can read and write, **not** a separate identity or role. The seed `task_goal` is published as `orchestrator`: the
caller of `publish` names the agent, so this check protects against a mistake, not against code that chooses to use another name.

## Run it

See [Run a library sample locally](../../README.md#run-a-library-sample-locally), then `python main.py`.

## Status

**Run** (real Cosmos DB, real `gpt-5-mini`): the sample ran as shipped. All four facts (`task_goal`, `research_findings`,
`analysis_summary`, `draft`) were stored with their owners and no raw id was in any container. In the earlier tests a second user
read nothing, a fact was readable by the next stage immediately (0 stale reads in 5 tries), and a failing stage 2 stopped the
loop and a clean re-run left no duplicate keys.

**Not run:** a hosted version, a process killed mid-call, whether the model quality is good (the researcher's reply was just "Done."
because its work went into a fact; the writer's brief read well).
