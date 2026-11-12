# ADR catalog

One entry per decision record. Fill `TEMPLATE.md` once per entry that applies to the chosen agent
type, write it to `<output-dir>/docs/adr/NNNN-<slug>.md`, and number them `0001`, `0002`, ... in the
order below. Entries marked "alone" are never merged with another.

Rules for every record:

* `{{DECISION}}` is the user's actual answer. `{{OPTIONS}}` lists the choices from the entry, with the
  chosen one marked, using the consequence column verbatim.
* `{{REASON}}` is the user's own words from the confirmation gate. If they gave none, write exactly
  `Reason not recorded.` Never invent a reason.
* `{{RELATED}}` links the other records this one depends on, by filename. Everything links to
  `0001-agent-type.md`.
* `name`, `description` and `output-dir` are facts, not decisions. They go in the agent README, not here.

---

## agent-type (alone, always)

* Applies to: prompt, hosted. Slug `agent-type`. Always `0001`.
* Question: prompt agent or hosted agent?
* Answers captured: `type`.

| Option | Consequence |
|---|---|
| prompt | Declarative: model, instructions and tool wiring only. No container, no code to maintain. Behavior can only change through `instructions.md` and the tool block. |
| hosted | Your Python (Microsoft Agent Framework) code runs in a Foundry container. Full control over logic and tools, but you own the code, its dependencies and its deploy/role setup. |

## model-runtime (together)

* Applies to: prompt, hosted. Slug `model-runtime`.
* Question: which model, which version, how much reasoning?
* Answers captured: `model`, `model-version` (hosted), `reasoning-effort` (prompt).
* Grouped because the version belongs to the model and reasoning effort only applies to reasoning models.

| Option | Consequence |
|---|---|
| Deploy-time placeholder (prompt default) | The model is chosen per environment at deploy. One definition serves dev and demo, but nothing in the repo says which model ran. |
| Pinned deployment name | Reproducible, but the deployment must exist in every target project. |
| Hosted: model named in `azure.yaml` | `azd provision` would create it if absent; with an existing project, name a deployment that already exists and do not provision. |
| Reasoning effort low / medium / high (prompt) | Higher effort raises quality and latency/cost. Omit it for non-reasoning models. |

## data-access (alone)

* Applies to: prompt (`grounding`), hosted (`tools`). Slug `data-access`.
* Question: where does the agent get its facts, and what can it call?
* Answers captured: prompt `grounding`; hosted `tools`.
* Alone because the eval `tool_definitions` must match it, so link `evaluation` to it.

| Option | Consequence |
|---|---|
| fabric (prompt) | A published Fabric data agent, queried on behalf of each caller. Needs a Fabric connection at deploy. |
| mcp (prompt) | A remote MCP server. Needs its URL and auth at deploy. |
| custom (prompt) | A function tool the consuming app implements. The agent cannot answer tool questions on its own. |
| none (prompt or hosted) | Model-only. No tool evaluators, no grounding checks. |
| sample (hosted) | One placeholder `echo` tool in `agent.py`. Replace it, and keep `evals` tool definitions in sync. |

## behavior-boundaries (together)

* Applies to: prompt, hosted. Slug `behavior-boundaries`.
* Question: what may the agent do, what must it refuse, how must it answer?
* Answers captured: prompt: read-only vs actions, must-ground vs general knowledge, scope limit, response style. Hosted: the `instructions` text.
* Grouped because all of it lands in `instructions.md` and is reviewed as one contract.

| Option | Consequence |
|---|---|
| Read-only | The agent must say so and refuse to act. Safest default; users cannot delegate actions to it. |
| Can take actions | Needs explicit action limits in the instructions. Wrong instructions can cause real side effects. |
| Must ground every answer | Fewer invented facts, more "I don't know" replies. |
| May use general knowledge | More helpful answers, harder to trust and to evaluate. |
| Hard scope limit | Off-domain questions are refused. The eval set needs `out_of_scope` cases. |

## evaluation (together)

* Applies to: prompt, hosted. Slug `evaluation`. Related: `data-access`.
* Question: how is quality measured, and what blocks a deploy?
* Answers captured: `evals` yes/no, test categories, which metrics gate.
* Grouped because the categories and gating metrics only mean something together.

| Option | Consequence |
|---|---|
| Evals on (default) | Datasets, evaluators and a runner ship with the agent. Costs model quota on each run. |
| Evals off | Nothing measures regressions. Prompt agents also lose the deploy gate. |
| `task_adherence` not gating | It can false-fail a correct tool call. Still reported. Re-enable once stable. |
| Gate on safety, quality, tool metrics | A miss fails CI (prompt) or is reported by the runner (hosted). |

## pipeline (together, prompt only)

* Applies to: prompt. Slug `pipeline`. Related: `evaluation`.
* Question: deploy by pipeline, and through which environments?
* Answers captured: `pipeline` yes/no, `environments` (default dev, demo).
* Grouped because the environments exist only because of the pipeline.
* Hosted agents get no pipeline today; do not write this record for them.

| Option | Consequence |
|---|---|
| GitHub Actions deploy, eval gate, promote | Repeatable, gated deploys. Needs OIDC Azure login and per-environment variables. |
| Manual deploy | No setup, but no gate and no audit trail. |

## container-sizing (alone, hosted only)

* Applies to: hosted. Slug `container-sizing`.
* Question: how much CPU and memory?
* Answers captured: `cpu`, `memory`.
* Alone because it is a cost and performance call revisited on its own schedule.
* Valid tiers per the current `azd`: `0.25 / 0.5Gi`, `1 / 2Gi`, `2 / 4Gi`.

| Option | Consequence |
|---|---|
| 0.25 / 0.5Gi (default) | Cheapest. Fine for a POC. Cold start took about 12 s in the POC. |
| 1 / 2Gi or 2 / 4Gi | More headroom for heavier tools or concurrency, at higher cost. |

---

## Deployment decisions (written at deploy time, not at the questionnaire)

Write these only when the action actually happened, after asking the user. Numbered after the
questionnaire records. Each is its own record because it is a one-off choice with its own risk.

### agent-identity-role (alone, hosted)

* Slug `agent-identity-role`. Related: `agent-type`, `data-access`.
* Why it exists: the deployed agent runs as its own identity (`<account>-<project>-<agent>-AgentIdentity`) with no roles, so model calls fail with `401 ... lacks the required data action`. Local runs hide this because they use the developer's credentials.
* Capture: the principal id, the role granted, the scope.
* Default decision seen in practice: `Foundry User` on the Foundry account.

| Option | Consequence |
|---|---|
| Foundry User on the account | Smallest built-in role that includes the model-call data action. Applies to every project under that account. |
| Narrower custom role | Least privilege, but more to maintain. |

### existing-project-reuse (alone, hosted or prompt)

* Slug `existing-project-reuse`.
* Why it exists: reusing an existing Foundry project and model deployment avoids provisioning, but the agent inherits that project's quotas and access.
* Capture: project endpoint, model deployment name. No secrets.

| Option | Consequence |
|---|---|
| Reuse existing project and deployment | No new cost for infrastructure. Shares quota and access with whatever else lives there. |
| `azd provision` a new project | Isolated, but creates billable resources and needs subscription and region sign-off. |
