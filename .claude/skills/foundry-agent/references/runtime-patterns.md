# Runtime patterns: MAF hosted baseline

Platform baseline for newly created hosted agents (plan Task 0). Local evidence only: nothing
here was deployed or run against Foundry.

## Pinned profile

| Item | Value | Provenance |
|---|---|---|
| Python | 3.13 (`python_3_13` in `azure.yaml`) | Local interpreter 3.13.14; pip dry-run resolves the set |
| `agent-framework-foundry` | 1.16.1 | PyPI release |
| `agent-framework-foundry-hosting` | 1.0.0b261106 (beta) | PyPI prerelease; source `microsoft/agent-framework@6b675d68` `python/packages/foundry_hosting` |
| Protocol | `responses` 2.0.0 | `responses/basic/agent.yaml` at the pinned commit; README says 1.0.0 is incompatible |
| Host | `ResponsesHostServer(agent=..., history_source="agent_server")` | `responses/basic/main.py` |

The hosting package is a beta. Bump it deliberately and re-run `hosted/tests/test_maf_template.py`.
The pinned source README warns some examples may precede published wheels; the installed wheel was
checked for the `agent_session_store_provider` and `history_source` parameters used here.

## Session manager

There is no enable flag. With `agent_session_store_provider` unset, `ResponsesHostServer` persists
`AgentSession` state in `FoundryAgentSessionStore` (Foundry storage when hosted, files under
`~/.agentserver/state_stores` locally). Bypasses: passing `agent_session_store_provider`, or a
caller sending `store=False` (one-shot, nothing persisted).

`hosted/assets/preflight.py` checks the first bypass in source and fails closed (`unknown`) when it
cannot tell. It does not inspect a deployment.

## Ownership

| Concern | Owner |
|---|---|
| Session/history persistence, user+sandbox scoping | Platform (Foundry + hosting package) |
| Invocation protocol, streaming, continuation | SDK hosting adapter; no custom code here |
| Instructions, specialists, `run_turn` test facade | Application: this skill's templates |
| Routing, refusal and the finite workflow | Application: the router template |
| Case state, semantic memory, tenant authorization | Separate decisions, not covered |

Platform defaults are not application controls. A managed session store does not prove tenant isolation,
a finite workflow does not bound retries or cost, and a refusal message is not safe termination of a
business action. The runtime contract lists these separately and marks nothing `passed` without
deployed evidence.

## Patterns

| Pattern | Status | Notes |
|---|---|---|
| `single-agent` | runnable-unvalidated | Created by the base hosted flow (MAF) or the prompt flow. |
| `router-specialists` | runnable-unvalidated | Tool-less. A classifier picks exactly one specialist or the request is refused. Created by `overlay/intake.py` / `overlay/scaffold_overlay.py`. The route recorded is the executor that ran, never the answer text. |
| `supervisor-worker` | design-only | Semantics, controls and validation are not defined. |
| `agent-handoffs` | design-only | Transfer, state, identity and termination contracts are not defined. |

Calling a specialist while keeping control is not a hand-off. A tool or A2A endpoint existing does not
establish control-transfer semantics.

## Approval requirements

Human approval and resume is cross-cutting, not a pattern. Declaring `requires_approval` on a tool, or
recording an approval requirement in the spec, **enforces nothing**: it appears in the contract as a
declared requirement with no enforcement mechanism. Router tools and action approvals are not generated
at all; ask for a design-only proposal instead.

## Router hosting shape

The router runs as a native MAF workflow hosted with `ResponsesHostServer(workflow=..., parse_response=...)`
and a factory that builds a fresh workflow per request. Wrapping a workflow as an agent is deprecated by
the hosting package because it shares state across requests. Leave the host's storage arguments unset;
`preflight.py` accepts exactly this shape and reports a storage override as `disabled` and anything it
cannot recognise as `unknown`.

## Unverified (Phase 2, needs an authorized deployment)

Effective session enablement when hosted, multi-turn continuity, restart persistence and
user/tenant isolation. The 23 local tests use a hand-written fake model client and prove wiring only.

## Local model checks

For a bare model call outside a hosted agent, do not use the project-scoped Responses URL
(`.../api/projects/<project>/openai/v1/responses`): it expects an agent/conversation context and returned
`409 A response is already in progress` for a plain model request on the inspected project. Chat
completions through the project's OpenAI-compatible client, or the resource-level Responses URL, work.
Whether the generated `main.py` works when hosted (where Foundry supplies that context) has not been
verified and needs an authorized deployment.
