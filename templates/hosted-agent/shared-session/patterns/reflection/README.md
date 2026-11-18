# Sample: reflection (writer and critic)

A writer drafts, a critic reviews, the writer revises, up to a round limit. The critic never sees the writer's conversation: the only
thing that crosses is the draft. **This is a library sample, not a hosted template.** Read the [parent README](../../README.md) first.

## How it works

| Agent | History | What it sees |
|---|---|---|
| `writer` | Private, in Cosmos | Its own conversation, so it remembers earlier feedback |
| `critic` | None (`private_history=False`) | Only `Review this draft:` plus the draft |

`MAX_ROUNDS` bounds the loop. **Do not rely on `APPROVED` to stop it.** In nine real reviews the critic never wrote that word, so
the loop normally runs to the limit. One later run did write it, in the third round. A reply that begins `REJECTED` does not
match, so the loop correctly continues.

## What the tests found, and what the sample does about it

| Finding | What the sample does |
|---|---|
| A blank draft was approved in 2 of 9 trials | Refuses to review a blank draft and stops |
| The critic and writer publish facts (`draft`, `review_verdict`) because `policy.yaml` allows those keys | Nothing. Remove the keys from `policy.yaml` if you do not want them stored |
| A writer turn is stored as four history documents, not two, because of the tool-call round trip | Nothing; the history has no duplicates |
| The critic's input is only the draft (3 of 3 runs) | By design |

## Identity in this sample

| # | Identity | How it is handled here |
|---|---|---|
| 1 | **The end user** | `demo_scope()` hard-codes `demo-user`. **Replace it** with a scope built per request from the caller's validated Entra token. |
| 2 | **The identity that runs the sample** | `az login` locally (`APP_ENV=dev`); the managed identity when deployed. It needs *Cosmos DB Built-in Data Contributor* on the database. |
| 3 | **Model access** | `gateway_client.chat_client()` with an Entra token for `GATEWAY_TOKEN_SCOPE`. |
| 4 | **The tenant** | `TENANT_ID`. |

The writer and the critic run under the **same** identity. The critic is kept apart from the writer by what it is given, not by a
separate identity.

## Run it

See [Run a library sample locally](../../README.md#run-a-library-sample-locally), then `python main.py`.

## Status

**Run** (real Cosmos DB, real `gpt-5-mini`): the sample ran as shipped through three rounds and printed the final draft. In the
earlier tests the loop stopped at the round limit for 0, 1 and 3, a critic failure in round 2 left the writer able to continue, two
reflection runs in parallel did not cross-talk, only the writer's partition held history, and no raw id was stored in any container.

**Not run:** whether the critic approves a genuinely good draft (0 of 9 approvals in the first test, 1 in the shipped run), a hosted
version, a process killed mid-call.
