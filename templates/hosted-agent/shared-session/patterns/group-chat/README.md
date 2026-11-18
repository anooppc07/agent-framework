# Hosted agent: group chat

A hosted Agent Framework agent (`kind: hosted`, Responses 2.0.0). Several agents talk in **one room** and every participant reads
the whole transcript. A fixed round-robin order and a hard round limit keep it bounded. State lives in a Cosmos DB the PortCo
owns; model calls go through the AI Gateway. Read the [parent README](../../README.md) first.

## Use it when / do not use it when

Use it when a group of agents should reason together and the user wants to see the discussion.

Do not use it to keep one agent's information from another. **Anything said in the room is visible to every participant**, and
`policy.yaml` filters only the shared facts, not the room. Keep sensitive values out of the room: let specialists read them from
their own policy-filtered facts and say only conclusions. In a test, all four participants said they could not use a card number
the user typed, yet it was stored in the room's checkpoints.

## Guards that cannot be skipped

| Guard | Why | Where |
|---|---|---|
| `max_rounds` must be an integer of at least 1 | `None`, or leaving it out, **never stops** (it ran until a test cap); `0` is silently raised to `1`. Booleans and strings are refused too. | `build_room()` in `main.py` |
| One clearance level for the whole room | A PII-cleared agent must not share a room with a public-only one. Checked in the factory **before any model call**. | `check_same_clearance()` in `main.py` |
| `output_from="all"` | Without it the caller receives only *"The group chat has reached the maximum number of rounds"*, not what the participants said. | `build_room()` |
| A blank message gets a friendly reply and ends the turn | The model is not called. It is handled **inside the same workflow** by a middleware on every agent (`app/blank_input.py`) and a termination condition that stops the room after the reply. A separate "blank" workflow would not work: see below. | `BlankInputMiddleware`, `ended_on_blank_input()` |

**Why not a separate workflow for blank input.** The host stores a hash of the workflow's graph with each conversation and refuses to
continue a conversation with a different one. An earlier version returned a different workflow under the same name for blank input.
A blank **first** message then killed the conversation (every later message failed with *"The stored workflow graph is
incompatible"*), and a blank message in the **middle** failed outright. Both were reproduced on the real host, and fixed by keeping
the graph identical for every turn.

A room that fails while it is being built (for example the mixed-clearance error) reaches the caller as a generic *"Native workflow
preparation failed"*. The reason is only in the server log. Keep the room defined in code, not in request input.

## Follow-up messages: a room is effectively single-turn

**`MAX_ROUNDS` is a budget for the whole conversation, not per message.** The round count is kept with the conversation's
checkpoint, so it keeps adding up across the user's messages. Measured on the real host with `MAX_ROUNDS = 8`:

| Message | What the caller got |
|---|---|
| 1st message | 8 speaker turns, then *"The group chat has reached the maximum number of rounds."* |
| 2nd and later messages | **One** speaker's reply, then the same *"reached the maximum number of rounds"* line |

So the first message uses the budget, and every later message gets a single reply. A blank message uses only one round, because the
room stops right after the friendly reply, so a blank first message does not spend the budget.

What to do about it:
- If the room is a one-shot discussion (one question, one decision record), nothing: that is how it is built.
- If users must be able to follow up with a full discussion each time, a bigger `MAX_ROUNDS` only delays the same cliff and makes every
  first message cost more model calls (this sample room always uses every round it is given). Resetting the count per message
  means writing the orchestrator's private round counter, which was **not tried**, so this template does not do it. Use a new
  conversation for a new question instead.

## Edit this

`ROOM` and `MAX_ROUNDS` at the top of `src/agent/main.py`. Every name must be an agent in `config/policy.yaml`, and all of them must
share one `max_classification`. The sample room is a researcher, an analyst, a reviewer and a writer, all `internal`. Do not add
`critic`: it is capped at `public` and the room is refused.

## Identity in this template

| # | Identity | How it is handled here |
|---|---|---|
| 1 | **The end user** | `scope_from()` takes the host's trusted `request.scope.user_id` (a hosted request without one raises). The conversation id is hashed with it, so someone else's conversation id gives an empty, separate room (tested). |
| 2 | **The agent's identity** | *Cosmos DB Built-in Data Contributor* on the one database, granted by `../../hooks/postdeploy.sh` from the agent's `instance_identity.principal_id`. |
| 3 | **Model access** | `gateway_client.chat_client()` signs in as the agent and sends an Entra token for `GATEWAY_TOKEN_SCOPE` to the AI Gateway. |
| 4 | **The tenant** | `TENANT_ID`, hashed into every key. |

**Every participant runs as the same agent identity.** There is no per-participant Azure identity. What separates participants is
the `policy.yaml` entry for each name, which controls only which facts each one can read and write.

## Fill in

`GATEWAY_MODELS_BASE_URL`, `GATEWAY_MODEL_NAME`, `GATEWAY_TOKEN_SCOPE` (DataX supplies all three), `TENANT_ID`, `SCOPE_HASH_PEPPER`
(Key Vault, 32+ characters), and for the Terraform layer `COSMOS_ACCOUNT_NAME`, `PRIVATE_ENDPOINT_SUBNET_ID` and
`COSMOS_PRIVATE_DNS_ZONE_ID` (the last two come from the platform stamp); see the parent README's Azure setup table.

## Run it locally

```bash
cd src/agent
cp .env.example .env            # fill in; set APP_ENV=dev so az login is used
uv sync && uv run python main.py
curl -s localhost:8088/responses -H 'content-type: application/json' -H 'x-agent-user-id: alice' \
     -d '{"input":"Decide whether to move our nightly batch jobs to Azure Container Apps Jobs.","conversation":{"id":"c1"}}'
```

The first message in a room of eight rounds took roughly a minute and 8 model calls in tests (later messages: see *Follow-up messages*).

## Status

**Run** (real Cosmos DB, real `gpt-5-mini`, the real `ResponsesHostServer`):
- The hosted room completed: 21 output items, with each participant's reply visible to the caller, ending with the round-limit line.
- A second user using the first user's conversation id got a separate room.
- `max_rounds` of `None`, `0`, `-1`, `True` and `"3"` were refused, and a mixed-clearance room was refused with no model call.
- No raw user id, tenant or conversation id was in any container. The card number typed by the user **was** stored in the checkpoints.
- **Blank input, in a conversation:** a blank first message got the friendly reply and ended the room after one speaker (2 messages), and the
  next real message got a full answer. A blank message in the middle of a conversation got the friendly reply, and the next real message
  was answered. No host errors. Before the fix, both cases failed: a blank first message made every later message fail, and a blank
  mid-conversation message failed outright.
- **Follow-up messages:** measured as described in *Follow-up messages* (8 speaker turns, then one reply per message).

**Not run:** a restart in the **middle** of a room (the handoff template has this; group chat does not), `azd up`, the post-deploy
hook, the Terraform (never validated or applied), the AI Gateway, a real managed identity, a request sent with `"stream": true` (every
test request was non-streamed; the host ran the *agents* as a stream anyway, and that is the path the blank-input middleware was
tested on), an LLM-chosen speaker, rooms of more than four participants.

**Size.** Two rooms produced 38 checkpoint documents, about 2 MB. Set the checkpoint TTL on purpose.

## Files

| File | Role |
|---|---|
| `src/agent/main.py` | The room, its guards, hosting, scope, the room-ending rule for a blank message |
| `src/agent/app/blank_input.py` | The middleware that answers a blank message without calling the model (shared with `handoff`) |
| `src/agent/app/`, `config/`, `gateway_client.py` | Copies of `../../common/`. Do not edit here; run `sh ../../sync-common.sh` |
| `azure.yaml`, `infra/foundry/` | `azd` manifest and the Foundry layer |
| `../../infra/cosmos/`, `../../hooks/` | The Cosmos Terraform and the post-deploy role grant, shared with `handoff` |
