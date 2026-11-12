---
description: 'Interview script the foundry-prompt-agent skill runs to collect scaffolding inputs'
---

# Interview Questions

Ask only for inputs not already supplied as arguments. Confirm the required ones before writing any
file. Keep it conversational — one short batch, sensible defaults called out.

## 1. Identity (required)

* **Agent name?** Kebab-case. Becomes the folder, the `agent_reference.name`, and the deploy
  `-AgentName`. (e.g. `claims-steward`, `policy-advisor`)
* **One-line description?** What it does and for whom. Goes verbatim into the agent definition.
* **Output directory?** Default `src/agents/<name>/`. Where the agent folder is written.

## 2. Model and reasoning (optional)

* **Pin a model deployment now, or leave it as a deploy-time variable?** Default: deploy-time
  (`__AZURE_AI_MODEL_DEPLOYMENT_NAME__`).
* **Reasoning effort?** `low` / `medium` / `high`, or none for a non-reasoning model. Default
  `medium`.

## 3. Grounding (required)

* **How does the agent get its data?**
  * `fabric` — a published Microsoft Fabric data agent (OBO per caller). *(default)*
  * `mcp` — a remote MCP server.
  * `custom` — a function tool implemented by the consuming app.
  * `none` — model-only, no tools.
* If `fabric`/`mcp`: you'll wire the connection id / server URL at deploy time (kept as placeholders
  now). Confirm the runtime **tool name** for the eval `tool_definitions` (Fabric →
  `DataAgent_<ConnectionName>`).
* If `custom`: function name, description, and parameters.

## 4. Behavior contract (required for good instructions)

* **Is the agent read-only, or can it take actions?** Read-only agents must say so explicitly.
* **Must every answer be grounded in the tool**, or may it answer from general knowledge?
* **Any hard scope boundary?** (e.g. "only answers about the active claim", "refuse off-domain
  questions")
* **Response style?** Length, tone, formatting rules.

## 5. Evaluation suite (required yes/no)

* **Add the Foundry evals suite?** Default **yes**. Scaffolds datasets, evaluators, orchestration,
  and offline unit tests.
* If yes: what are the **test categories** for this domain? (e.g. `grounding`, `no_grounding`,
  `read_only_refusal`, `out_of_scope`, `tool_usage`, `response_style`). Defaults provided; tailor to
  the agent.

## 6. CI/CD pipeline (required yes/no)

* **Add the GitHub Actions deploy pipeline?** Default **yes**. Scaffolds `deploy → eval-gate →
  promote`.
* If yes: which **environments**? Default `dev, demo` (deploy dev, gate on evals, then demo).
* Confirm the repo uses **OIDC** Azure login and per-environment variables (the template assumes
  both).

## Confirmation gate

Before writing files, restate: `name`, `output-dir`, `grounding`, `evals` (yes/no), `pipeline`
(yes/no), and `environments`. Only proceed on explicit confirmation.
