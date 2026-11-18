# Hosted orchestration: sequential chain or group chat

A hosted agent (Agent Framework, Responses protocol) that runs several agents together. Pick the pattern with
`ORCHESTRATION_PATTERN` in `azure.yaml`: `sequential` (the default) or `group-chat`. Models are reached only
through the AI Gateway, like every other hosted template ([GATEWAY-CONTRACT](../../_gateway/GATEWAY-CONTRACT.md)).

## The decision this template encodes: no shared session

Each agent keeps **its own `AgentSession`**. The orchestrator forwards the conversation between them. The
template does not share one session across agents, because on the pinned versions that:

| Measured on the pinned wheels (fake model) | Result |
|---|---|
| Two agents share one session in a sequential chain | The second agent's model receives the first agent's turn **twice** |
| Three agents share one session in a group chat | A participant receives the same message **twice** per call |
| Restore from a checkpoint into a freshly built workflow | The sharing is **lost**: each executor restores its own session copy |

`AgentExecutor`'s own docstring says its session "is not tied to a caller". Passing a session id string to
another agent shares nothing either: with the default in-memory history the messages live inside the session
object, not under its id. See `docs/handoff/HANDOFF.md` for the full 13-case spike and the workarounds that
were rejected.

If you need state that outlives one request or crosses agents, keep it in a store you own
([`custom-store`](../custom-store)) and pass each agent only the part it needs.

## What it does

| Pattern | Flow | The caller receives |
|---|---|---|
| `sequential` | input gate, then `drafter`, then `reviewer` | the reviewer's reply |
| `group-chat` | round-robin between `drafter` and `reviewer`, at most `MAX_ROUNDS` (6) turns | every turn, then MAF's closing line |

Edit `make_workflow()` in `src/agent/main.py` to change the agents, instructions, or the group chat's selector.

## Behaviour you can rely on

Each row below is covered by a test that ran green (see [Verification](#verification)).

- **Blank input makes no model call.** Sequential answers `REJECTED: input must be a non-empty string`. A
  group chat stops without calling a participant and returns MAF's termination line, not `REJECTED:` (the group
  chat builder has no slot for a gate). Both complete normally. Raising instead reaches the caller as a generic
  `server_error` that looks like an outage.
- **`max_rounds` is required and must be at least 1.** MAF treats `None` as unbounded and silently raises 0 to
  1, so the template rejects both.
- **A failure resumes at the failed agent.** After agent C fails, a resume from the last checkpoint calls only C.
  Do **not** wrap the orchestration in an outer workflow to add a step: that makes a resume re-run every
  earlier agent (measured: `[a, b, c]` instead of `[c]`), which repeats model calls and any side effects.
- **A fresh workflow is built per request** with stable executor ids, as the host requires.
- **The host's default Foundry-managed stores are used.** `main.py` passes only `workflow=` and
  `parse_response=`, and the repo's `preflight.py` returns `enabled` for it.

## Fill in (DataX gives you these; none is in the architecture KB)

`GATEWAY_MODELS_BASE_URL`, `GATEWAY_MODEL_NAME`, `GATEWAY_TOKEN_SCOPE`. Set `ORCHESTRATION_PATTERN` in
`azure.yaml` to `sequential` or `group-chat`. Replace the agents and their instructions in `make_workflow()`
(`src/agent/main.py`). In a group chat every agent sees the whole conversation, so give each a different job
and tell it not to repeat earlier messages; overlapping instructions made a real model repeat the same line
three times in six turns.

## Run locally against the fake gateway

```bash
python ../../_gateway/fake_gateway.py 8099 &
cd src/agent
export GATEWAY_MODELS_BASE_URL=http://127.0.0.1:8099/v1 GATEWAY_MODEL_NAME=logical-model \
       GATEWAY_TOKEN_SCOPE=https://management.azure.com/.default \
       OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false ENABLE_SENSITIVE_DATA=false \
       ORCHESTRATION_PATTERN=sequential          # or: group-chat
uv sync && uv run python main.py
curl -s localhost:8088/responses -H 'content-type: application/json' \
     -d '{"input":"Write a launch email","conversation":{"id":"c1"}}'
curl -s localhost:8088/responses -H 'content-type: application/json' -d '{"input":"   "}'   # expect REJECTED
```

The fake gateway is not a model: it replies `[fake-model] <last user text>`, so a sequential chain returns the
reviewer's echo. It prints one line per request showing whether a bearer token arrived; every model call should
appear there, and none should go anywhere else (R1).

The tests need no gateway and no login. They use a hand-written fake model and drive the real
`ResponsesHostServer` in-process:

```bash
cd src/agent
uv sync && uv pip install pytest pytest-asyncio
pytest                                   # 42 tests
```

If your home directory is read-only, set `AGENTSERVER_STATE_ROOT` to a writable path: the host's default
response store writes under `~/.agentserver`. Set `APPLICATIONINSIGHTS_CONNECTION_STRING=` empty to stop the
tests sending telemetry.

## Deploy (dev project only)

`azd env set` the three gateway variables, then `azd up` and `azd ai agent invoke "hello"`. Not tried: see Status.

## Status

**Run:** 42 tests pass on the pinned versions (`agent-framework-foundry` 1.16.1,
`agent-framework-foundry-hosting` b261106, `agent-framework-orchestrations` 1.4.2, core 1.26.0) against a
hand-written fake model, driving the real `ResponsesHostServer` over ASGI. The tests cover the sequential and
group-chat paths, blank input, `max_rounds`, failure and resume from a checkpoint, interleaved requests, and
tenant partitioning. Mutation checks confirmed the tests fail when the behaviour they guard is broken. The
repo's `preflight.py` returns `enabled` for `main.py` and `_gateway/check_templates.py` reports no gateway
bypass. One request per pattern was also sent to a real `gpt-5-mini` deployment, directly and not through the
gateway: both returned real replies, and blank input behaved as documented. That run used the earlier, shared
group-chat prompts (see the prompt note under Fill in).

**Not run:**
- **The fake-gateway run above.** It needs a Microsoft Entra login that the environment this was written in
  could not provide, so the `curl` commands are untested. Run them and check the gateway log.
- **Against the AI Gateway, or deployed.** No `azd up`, no Foundry project. Hosted persistence (Foundry-managed
  session and checkpoint stores) is untested: restore and resume were tested on in-memory storage only.
- **The current group-chat prompts** against a real model.
- **Isolation between users.** MAF's hosting docs and the Foundry samples disagree on whether `agent_session_id`
  is a trusted boundary (see the handoff). Do not treat a session or response id as authorization.
- **Group chat rejects blank input with MAF's wording**, not `REJECTED:`: `GroupChatBuilder` has no slot for a
  gate.
- **Handoff and Magentic** are not covered. **Newer releases** (foundry 1.16.2, hosting b261112) were not tried.
- **The pin is local.** `agent-framework-orchestrations==1.4.2` is in this template's `pyproject.toml` only.
  The shared `.claude/skills/foundry-agent/hosted/assets/requirements.txt` is unchanged.
- **`azd`** was not run; `azure.yaml` parses as YAML and its schema was not validated.

## Optional: tenant-partitioned checkpoints (`tenant_store.py`)

`TenantCheckpointStorage` wraps any MAF `CheckpointStorage` so one tenant/user cannot list, load or delete
another's checkpoints. It hashes `(tenant_id, user_id)` into the partition and never writes the raw ids.

**It is not wired into `main.py` and cannot be.** The host owns checkpoint storage, and overriding
`checkpoint_store_provider` makes the repo's preflight fail. Use it only in a `custom-store` style deployment
where the PortCo owns the store and has signed off on the exception. It is tested against in-memory storage only.

## Files

| File | Role |
|---|---|
| `src/agent/orchestration_flow.py` | `build_sequential`, `build_group_chat`, the input gate, `validate_input` |
| `src/agent/main.py` | `build_server()`, `parse_response`, `make_workflow()` (edit this) |
| `src/agent/tenant_store.py` | Optional tenant-partitioned checkpoint wrapper |
| `src/agent/gateway_client.py` | Vendored copy of `_gateway/gateway_client.py`. Keep them identical |
| `src/agent/tests/` | 42 tests, fake model in `fakes.py` |
