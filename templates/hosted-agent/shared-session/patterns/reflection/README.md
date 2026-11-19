# reflection: a writer and a critic that iterate

A writer drafts, a critic reviews, the writer revises, up to a round limit. The critic never sees the writer's conversation: the only
thing that crosses is the draft. Read the [parent README](../../README.md) first.

```
writer -> "Draft a 100-word announcement for our new offline mode."
critic (sees ONLY the draft) -> "APPROVED"   or   up to 3 specific fixes
writer -> revised draft          (repeats, at most MAX_ROUNDS = 3)
```

## Use it when / do not use it when

Use it when a second opinion should judge the work on its own merits, without the writer's reasoning.

Do not rely on `APPROVED` to stop the loop. In nine real reviews the critic never wrote that word, so the loop normally runs to
`MAX_ROUNDS`. One later run did write it, in the third round.

## How it works

| Agent | History | What it sees |
|---|---|---|
| `writer` | Private, in Cosmos | Its own conversation, so it remembers earlier feedback |
| `critic` | None (`private_history=False`) | Only `Review this draft:` plus the draft |

`MAX_ROUNDS` bounds the loop. A reply that begins `REJECTED` does not match `APPROVED`, so the loop correctly continues.

## What the tests found, and what the sample does about it

| Finding | What the sample does |
|---|---|
| A blank draft was approved in 2 of 9 trials | Refuses to review a blank draft and stops |
| The critic and writer publish facts (`draft`, `review_verdict`) because `policy.yaml` allows those keys | Nothing. Remove the keys from `policy.yaml` if you do not want them stored |
| A writer turn is stored as four history documents, not two, because of the tool-call round trip | Nothing; the history has no duplicates |
| The critic's input is only the draft (3 of 3 runs) | By design |

## Run it

Set up Cosmos and your environment as in [Run a pattern locally](../../README.md#run-single-agent-reflection-sequential-pipeline-or-agent-as-tool-locally). Then:

```bash
cd patterns/reflection
python main.py
```

## Identity

The four identities are in the [parent README](../../README.md#identity-who-is-who). What differs here: `demo_scope()` hard-codes
`demo-user`; **replace it** with a scope built per request from the caller's validated Entra token.

The writer and the critic run under the **same** identity. The critic is kept apart from the writer by what it is given, not by a
separate identity.

## Status

**Run** (real Cosmos DB, real `gpt-5-mini`): the sample ran as shipped through three rounds and printed the final draft. In the
earlier tests the loop stopped at the round limit for 0, 1 and 3, a critic failure in round 2 left the writer able to continue, two
reflection runs in parallel did not cross-talk, only the writer's partition held history, and no raw id was stored in any container.

**Not run:** whether the critic approves a genuinely good draft (0 of 9 approvals in the first test, 1 in the shipped run), a hosted
version, a process killed mid-call.
