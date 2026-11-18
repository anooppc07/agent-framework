# Hosted agent: human in the loop (Agent Framework)

An Agent Framework workflow (`kind: hosted`, Responses 2.0.0) that **drafts a proposal, pauses for a human
review, and takes approve, revise or reject**. The model is reached through the AI Gateway, never the
project endpoint.

```
task -> drafter -> reviewer --approve--> publish the draft
           ^          |
           +--revise--+   (feedback goes back to the drafter; at most 5 rounds)
                      +--reject--> "REJECTED: no draft was published."
```

The workflow pauses with `ctx.request_info(...)` and resumes in a `@response_handler`. See `review_flow.py`.

## The client contract

| Step | What the client sees or sends |
|---|---|
| Pause | A `function_call` item named `request_info`. Its `arguments` are `{"request_id": <call_id>, "request": {"draft": ..., "round": n}}` |
| Approve | `function_call_output` with `{"action": "approve"}` |
| Revise | `function_call_output` with `{"action": "revise", "feedback": "..."}` |
| Reject | `function_call_output` with `{"action": "reject"}`. The response **completes** and its text is `REJECTED: no draft was published.` |

A rejection is a normal, clearly marked result and not an error, because this host turns any exception into a
generic `server_error` that a client cannot tell from an outage. Reply with the same `conversation.id` and the
`call_id` of the pause:

```json
{"conversation": {"id": "c1"},
 "input": [{"type": "function_call_output", "call_id": "<call_id>", "output": "{\"action\":\"approve\"}"}]}
```

A reply with an unknown `call_id`, an unknown `action`, or a duplicate is refused before any handler runs.
An empty `revise` or a sixth revision is treated as a reject. `main.py` branches on the reply item types
`function_call_output` and `mcp_approval_response`; a host that sends other item types needs `REPLY_ITEM_TYPES` extended.

## Things that will bite you

- **Checkpoint allow-list.** The host stores the pause in a checkpoint and refuses application types that are not
  listed. `main.py` lists `Job`, `ReviewRequest` and `Decision`. Add a type only if it is yours: checkpoints are
  pickled, and the store must be writable only by the agent identity.
- **Workflow state must be saved.** `Reviewer.on_checkpoint_save/restore` keep the pending job across a restart.
- **A fresh workflow per request.** `make_workflow` builds new executors each time; do not share one.
- **Content stays out of traces.** `azure.yaml` sets both content variables to false; see `../observability/README.md`
  for why one is not enough.

## Fill in (DataX gives you these; none is in the architecture KB)

`GATEWAY_MODELS_BASE_URL`, `GATEWAY_MODEL_NAME`, `GATEWAY_TOKEN_SCOPE`. Replace the drafting prompt in
`review_flow.py`. For a *tool* approval instead of a draft review, use `@tool(approval_mode="always_require")`
with the host's approval store (see `../custom-store`).

## Run locally against the fake gateway

```bash
python ../../_gateway/fake_gateway.py 8099 &
cd src/agent
export GATEWAY_MODELS_BASE_URL=http://127.0.0.1:8099/v1 GATEWAY_MODEL_NAME=logical-model \
       GATEWAY_TOKEN_SCOPE=https://management.azure.com/.default \
       OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false ENABLE_SENSITIVE_DATA=false
uv sync && uv run python main.py
curl -s localhost:8088/responses -H 'content-type: application/json' \
     -d '{"input":"Write a launch email","conversation":{"id":"c1"}}'
```

## Status

Run with `curl` against the fake gateway on Agent Framework core 1.25.0 and hosting 1.0.0b261106:
approve, revise then approve (round 2's draft is built from the feedback), reject, an unknown call id, an unknown
action, and an empty revise (treated as reject). A paused review also survived killing the process and
starting a new one (local file store). **Not run:** against Foundry, a real gateway, or a hosted deployment, so
persistence under Foundry's checkpoint store, two simultaneous reviewers, and answering the same pause twice
are untested. Not deployed: `azd` is not installed here.
