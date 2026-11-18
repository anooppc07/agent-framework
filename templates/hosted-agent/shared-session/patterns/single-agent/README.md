# Sample: single agent with durable memory

One agent that remembers a user across restarts. Its history is private to it, in Cosmos, and survives a new process.
**This is a library sample, not a hosted template**: you run it in a script, a job, or your own API. Read the
[parent README](../../README.md) first.

## Why it is not hosted

A hosted regular agent cannot tell its callers apart. The host builds the agent once, so it has one fixed scope, and no tool or
provider receives the trusted user. In a test, a second user who sent the first user's conversation id was shown the first user's
facts and messages. A hosted version would have to be a one-step workflow, whose factory does receive the request (see
[`../handoff/src/agent/main.py`](../handoff/src/agent/main.py)). **That version has not been run.**

## How it works

`main.py` builds an agent with `rt.agent(...)`. Its history key is a hash of tenant, user, conversation and agent name, so the
same conversation resumes after a restart. It can save stable preferences with `publish_fact` (allowed by `policy.yaml`).
Blank messages are refused before the model is called: the library would otherwise store an empty turn and still call the model.

## Identity in this sample

You run it, so you decide who the end user is.

| # | Identity | How it is handled here |
|---|---|---|
| 1 | **The end user** | `demo_scope()` hard-codes `demo-user`. **Replace it.** In an API, build `RequestScope` per request from the caller's validated Entra token (tenant and user from claims), never from the request body. Because the scope is part of every key, a wrong user id here puts one person's memory in another's partition. |
| 2 | **The identity that runs the sample** | Locally, `az login` through `DefaultAzureCredential` (`APP_ENV=dev`). Deployed, the host's managed identity through `ManagedIdentityCredential`. Either needs *Cosmos DB Built-in Data Contributor* on the database. |
| 3 | **Model access** | `gateway_client.chat_client()` sends an Entra token for `GATEWAY_TOKEN_SCOPE` to the AI Gateway. |
| 4 | **The tenant** | `TENANT_ID`. |

## Run it

See [Run a library sample locally](../../README.md#run-a-library-sample-locally). Then:

```bash
python main.py        # run it twice: the second run remembers the first
```

## Status

**Run** (real Cosmos DB, real `gpt-5-mini`): the sample ran as shipped. Its first turn saved the preference, the second recalled it
("You prefer answers in bullet points"), and the facts and history landed in Cosmos with no raw user, tenant or conversation id. In
the earlier tests a new process recalled the previous process's turn from history alone, a 20,000-character message and a
120-turn history were stored whole and in order, and a failed model call stored nothing for that turn.

**Not run:** a hosted version of this pattern, two turns sent to one session at the same moment (they did not duplicate in a test,
but the sample does not guard against it), a process killed mid-call.
