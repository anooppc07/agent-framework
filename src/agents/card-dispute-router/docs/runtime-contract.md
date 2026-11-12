# Runtime contract: card-dispute-router

* Kind: hosted
* Framework: maf (declared by the spec)
* Pattern: router-specialists (runnable-unvalidated)
* Mode: create-new
* Rationale: Dispute intake and suspected-fraud reports are independent topics, so separate specialists answer each.
* Evaluation: unconfigured
* Pipeline: selected-not-executed
* Claims implementation: no

## Evidence scope

Generated rows are declarations. Local tests and platform documentation do not establish deployed verification; only scoped deployed evidence with environment, version, test and run can.

## Controls

| Control | Implementation | Verification | Responsibility | Gaps |
|---|---|---|---|---|
| identity-propagation | declared | not-run | declared-requirement |  |
| approved-tool-access | declared | not-run | declared-requirement |  |
| context-memory-isolation | not-implemented | not-run | application | platform session defaults do not prove user or tenant isolation |
| bounded-reasoning-retries | not-implemented | not-run | application |  |
| handoff-controls | not-implemented | not-applicable: topology: a single-agent or router runs no control transfer between agents | application |  |
| safe-termination | not-implemented | not-run | application |  |
| traceability | declared | not-run | declared-requirement |  |
| audit-evidence | not-implemented | not-run | application |  |

## Session manager

* Required: enabled
* Declared: enabled
* Observed: not-observed
* Owner: platform (Foundry-managed session store via the SDK hosting integration)
* Blocks readiness: yes
* Provenance: microsoft/agent-framework 6b675d68 packages/foundry\_hosting README 'Agent Sessions'; pinned agent-framework-foundry-hosting release; hosted/assets/preflight.py (source check only)
* Separate concerns: semantic long-term memory, business-case state, tenant authorization, retention and deletion policy

## Capabilities and mechanisms

| Name | Responsibility | Basis | Summary | Limits |
|---|---|---|---|---|
| session-history | platform | provided-by-template | Foundry-managed session store keeps sessions and history; no custom session code is delivered. | retention, deletion, tenant authorization and business-case state are separate decisions |
| invocation-protocol | platform | provided-by-template | The standard SDK hosting adapter handles invocation, streaming and continuation. | no custom protocol code in this skill |
| context-propagation | platform | provided-by-template | Host-supplied conversation context reaches the application; specialist instructions stay scoped. | local context tests are not hosted isolation proof |
| bounded-routing | application | provided-by-template | Finite classify then at most one tool-less specialist run. | underlying SDK/HTTP retries, duration, cost, token limits and cancellation are not bounded |
| refusal | application | provided-by-template | Blank or unroutable requests have an explicit refusal outcome. | no timeout, exception or cancellation recovery; not safe business-action termination |
| release-evidence | shared | provided-by-template | Generated evaluation and pipeline artifacts are declarations until executed. | evaluating a candidate version is not evidence that every runtime or security control passed |
