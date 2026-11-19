# handoff: one conversation, the speaker changes

A hosted Agent Framework agent (`kind: hosted`, Responses 2.0.0). A **triage** agent hands a customer to **billing** or **tech
support**. The live conversation is shared by every participant, which is what makes it a handoff. State lives in a Cosmos DB
the PortCo owns; model calls go through the AI Gateway. Read the [parent README](../../README.md) first.

```
User:     "I was charged twice for order 8812. Please refund the duplicate 450 THB."
triage    -> publishes order_id and customer_intent, hands off to billing
billing   -> asks to run submit_refund   (the host pauses and returns an approval request)
User:     approves, in the same conversation
billing   -> "Refund queued for 8812: 450 THB"
```

The routes are `triage -> billing | tech_support`, and `billing` or `tech_support` can hand back to `triage`. The workflow is
`src/agent/workflows/handoff.py`.

## Use it when / do not use it when

Use it when the customer should feel one continuous conversation while the agent who answers changes.

Do not put two agents in one handoff if one of them must never see what the other discusses. **The handoff conversation is not
filtered by `policy.yaml`.** In a test, `tech_support` received billing's replies and the refund reason as text, even though the
policy hides `refund_reason` from it in the facts channel. Use separate services for such a pair.

## How it works

| Piece | File | What it does |
|---|---|---|
| Hosting | `src/agent/main.py` | `build_server()`, a per-request `workflow_factory`, `parse_turn` |
| Blank messages | `src/agent/app/blank_input.py` | A middleware on every agent answers a blank message with a friendly reply, without calling the model |
| The workflow | `src/agent/workflows/handoff.py` | triage, billing (with `submit_refund`, approval required), tech support |
| Shared facts | `src/agent/app/memory/` | policy-filtered facts the agents publish and read |
| Checkpoints | `src/agent/app/memory/checkpoints.py` | Cosmos checkpoints, one partition per tenant, user and conversation |
| Policy | `src/agent/config/policy.yaml` | who may read and write which fact; deny by default |

Edit the agents' instructions and the routing in `workflows/handoff.py`. `submit_refund` is a stub: replace it with your payments
call, and make that call idempotent on the order and amount, because an approval plus a resume guarantees **once per
checkpoint, not once in the world**.

## Identity

The four identities are in the [parent README](../../README.md#identity-who-is-who). What differs here: `scope_from()` takes the
host's trusted `request.scope.user_id`, and a hosted request without one raises. The conversation id may come from the caller, but
it is always hashed together with the trusted user, so **someone else's conversation id gives an empty, separate partition**
(tested: a second user got nothing from the first). `../../hooks/postdeploy.sh` (or `Set-CosmosAgentAccess.ps1` on Windows) grants
the agent's identity its Cosmos role on that one database.

Who may approve a refund: **the same user who started the conversation.** The host validates a reply against that user's
checkpoint before any tool runs. In a test, a second user who sent the first user's approval id was refused with *"Workflow
replies require an exact pending stored checkpoint"* and the refund did not run. There is no separate "approver" identity here. If
a different person (a supervisor) must approve, that needs its own design.

## Fill in

The settings are listed in the parent README under [Settings to fill in](../../README.md#settings-to-fill-in). This template reads
them from `src/agent/.env.example` (copy it to `.env`) and, when deployed, from `azure.yaml`.

## Run it locally

You need a Cosmos database with the three containers and your user granted the data role on it (see the parent README).

```bash
cd src/agent
cp .env.example .env            # fill in; set APP_ENV=dev so az login is used
uv sync && uv run python main.py
curl -s localhost:8088/responses -H 'content-type: application/json' -H 'x-agent-user-id: alice' \
     -d '{"input":"I was charged twice for order 8812. Please refund the duplicate 450 THB.","conversation":{"id":"c1"}}'
```

The response holds an `mcp_approval_request` for `submit_refund`. Approve it in the same conversation:

```bash
curl -s localhost:8088/responses -H 'content-type: application/json' -H 'x-agent-user-id: alice' \
     -d '{"conversation":{"id":"c1"},"input":[{"type":"mcp_approval_response","approval_request_id":"<id from above>","approve":true}]}'
```

When the workflow waits for the user's next message, the response holds a `function_call` named `request_info`. Answer it with a
`function_call_output` whose `output` is a JSON list of messages: `[{"role":"user","contents":[{"type":"text","text":"..."}]}]`.

**Blank input.** A blank message gets the reply *"I didn't receive a message. Please type your question."* as a normal completed
response, not a failure, and the workflow **keeps waiting for the user**: the response holds a new `request_info` to answer. This is done
inside the same workflow on purpose. An earlier version returned a separate "blank" workflow under the same name; the host stores a
hash of the workflow's graph with each conversation, so a blank **first** message made every later message fail with *"The stored
workflow graph is incompatible"*, and a blank message in the **middle** of a conversation failed outright. Both were reproduced on
the real host and fixed by keeping the graph identical for every turn. (The framework drops custom `additional_properties` on the
reply when it builds the message, so do not rely on a `blank_input` flag in the response.)

## Status

**Run** (real Cosmos DB, real `gpt-5-mini`, the real `ResponsesHostServer`, a new server instance for each turn):
- Turn 1 routed triage to billing, published facts, and paused for a refund approval. A restarted server approved it, the refund
  ran **exactly once**, and the workflow paused for the user. A third server answered that pause and the flow went on to tech support.
- A second user, using the first user's conversation id, started an empty conversation. None of the first user's facts reached
  the second user's model calls.
- **Blank input, in a conversation** (after the fix): a blank first message got the friendly reply, and the next real message got a real
  answer. A blank message in the middle of a conversation got the friendly reply and the workflow went on waiting; the next real message
  led to the refund approval request. No host errors. Before the fix both cases failed.
- No raw user id, tenant or conversation id was found in any container. The card number typed by the user **is** stored, raw, in
  `workflow-checkpoints`.

**Not run against this template's files:** a model failure. It was injected on the research build this template was made from
(the same workflow and host wiring), where the conversation was blocked for good and a new one worked. Also not run: `azd up`, the
post-deploy hook, the Terraform (never validated or applied), the AI Gateway, a real managed identity, a process killed
mid-call, a request sent with `"stream": true` (every test request was non-streamed; the host ran the *agents* as a stream anyway,
and that is the path the blank-input middleware was tested on).

## Files

| File | Role |
|---|---|
| `src/agent/main.py` | Hosting and scope |
| `src/agent/workflows/handoff.py` | The agents and the routing |
| `src/agent/app/`, `config/`, `gateway_client.py` | Copies of `../../common/`. Do not edit here; run `sh ../../sync-common.sh` |
| `azure.yaml`, `infra/foundry/` | `azd` manifest and the Foundry layer |
| `../../infra/cosmos/`, `../../hooks/` | The Cosmos Terraform and the post-deploy role grant, shared with `group-chat` |
