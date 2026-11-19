# single-agent: one agent that remembers a user

One agent whose history is private to it, in Cosmos, and survives a new process. Read the [parent README](../../README.md) first.

```
run 1:  "Hi, I prefer answers in bullet points."   -> the agent saves the preference
run 2:  "What format do I prefer?"                 -> "You prefer answers in bullet points."
```

## Use it when / do not use it when

Use it for a script, a job or your own API that serves one person at a time and should remember them.

Do not use it as a hosted agent. A hosted regular agent cannot tell its callers apart: the host builds the agent once, so it has one
fixed scope, and no tool or provider receives the trusted user. In a test, a second user who sent the first user's conversation id
was shown the first user's facts and messages. A hosted version would have to be a one-step workflow, whose factory does receive the
request (see [`../handoff/src/agent/main.py`](../handoff/src/agent/main.py)). **That version has not been run.**

## How it works

`main.py` builds an agent with `rt.agent(...)`. Its history key is a hash of tenant, user, conversation and agent name, so the
same conversation resumes after a restart. It can save stable preferences with `publish_fact` (allowed by `policy.yaml`).
Blank messages are refused before the model is called: the library would otherwise store an empty turn and still call the model.

## Run it

Set up Cosmos and your environment as in [Run a pattern locally](../../README.md#run-single-agent-reflection-sequential-pipeline-or-agent-as-tool-locally). Then:

```bash
cd patterns/single-agent
python main.py        # run it twice: the second run remembers the first
```

## Identity

The four identities are in the [parent README](../../README.md#identity-who-is-who). What differs here: **you decide who the end user
is.** `demo_scope()` hard-codes `demo-user`; **replace it.** In an API, build `RequestScope` per request from the caller's validated
Entra token (tenant and user from claims), never from the request body. The scope is part of every key, so a wrong user id puts one
person's memory in another's partition. It runs under `az login` locally (`APP_ENV=dev`) and the managed identity when deployed.

## Status

**Run** (real Cosmos DB, real `gpt-5-mini`): the sample ran as shipped. Its first turn saved the preference, the second recalled it
("You prefer answers in bullet points"), and the facts and history landed in Cosmos with no raw user, tenant or conversation id. In
the earlier tests a new process recalled the previous process's turn from history alone, a 20,000-character message and a
120-turn history were stored whole and in order, and a failed model call stored nothing for that turn.

**Not run:** a hosted version of this pattern, two turns sent to one session at the same moment (they did not duplicate in a test,
but the sample does not guard against it), a process killed mid-call.
