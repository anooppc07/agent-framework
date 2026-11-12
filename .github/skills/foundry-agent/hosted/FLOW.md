# Foundry Hosted Agent (flow)

> Runs after the router's Q0 chose **hosted** and the shared questions are answered (`../SKILL.md`).
> Paths below (`assets/`) are relative to this `hosted/` folder.

Thin scaffold layer. This skill only generates the agent source from templates. Everything after that
(provision, deploy, invoke, observe, troubleshoot, CI/CD) belongs to the **Microsoft Foundry skill** (`microsoft-foundry` in the azure-skills pack; install it for Copilot) —
invoke that skill and follow its sub-skills; do not re-implement them here.
When you hand off, scope that skill to `<output-dir>` only: tell it the agent root, and do not let its
workspace discovery step scan or pick other agent folders.

Hosted = your code runs in a Foundry-managed container. For a declarative agent with no code, pick the
**prompt** type in the router (`../SKILL.md`) instead.

## Inputs

Name, description, output-dir, model and evals were asked by the router. Ask the remaining
hosted-specific ones (`instructions`, `tools`, `model-version`, `cpu`/`memory`). Never invent a
required value.

| Input | Required | Default | Purpose |
|-------|----------|---------|---------|
| `name` | Yes | — | kebab-case agent + azd service name |
| `description` | Yes | — | One line (plain text, no `:`), goes into `azure.yaml` |
| `output-dir` | No | `src/agents/<name>/` | Where the folder is written |
| `model` | No | `gpt-5.4` | Model deployment name |
| `model-version` | No | `2026-03-05` | Model version (azure.yaml deployment) |
| `instructions` | Yes | — | System prompt text, written to `instructions.md` |
| `tools` | No | `none` | `none` or `sample` (one example `@tool` to replace) |
| `cpu` / `memory` | No | `0.25` / `0.5Gi` | Container resources |
| `evals` | No | `yes` | Scaffold the Foundry evaluation suite under `evals/` |

## Flow

1. **Resolve inputs** (table above). Confirm `name`, `output-dir`, `instructions` before writing.
2. **Copy `assets/` into `output-dir`**, substituting every `{{PLACEHOLDER}}` (list below).
   Substitute `{{INSTRUCTIONS}}` only in `instructions.md` (read at runtime by `agent.py`). If
   `tools=none`, remove the `echo` tool between the `sample-tool` markers in `agent.py` and leave
   `_tools = []`; if `sample`, keep it.
3. **Evals** (`evals=yes`, default): copy `assets/evals/` to `<output-dir>/evals/`, replace
   `{{AGENT_NAME}}`, and keep `evaluators/tools.py` `TOOL_DEFINITIONS` plus every dataset row's
   `tool_definitions` in sync with the tools in `agent.py` (the `name` must equal the function
   name, or `tool_call_accuracy` returns "Not applicable"). Rewrite the dataset rows for the real
   agent. Install `evals/requirements.txt` + `pytest` in the agent venv and run
   `python -m pytest tests/ -v` from `evals/`; all must pass before handoff. For a tool-less agent
   follow "Agent without tools" in `evals/README.md`.
4. **Decision records.** Follow "Decision records" in `../SKILL.md`: `agent-type`, `model-runtime`,
   `data-access`, `behavior-boundaries`, `evaluation`, `container-sizing`. Skip `pipeline`. Add
   `docs/` to the agent's `.agentignore` so the records are not zipped into the deploy.
5. **Sweep**: `grep -rnE "\{\{[A-Z][A-Z0-9_]*\}\}" <output-dir>` must return nothing
   (`{{item.*}}` / `{{sample.*}}` in `evals/` are Foundry templates, not placeholders).
   Deployment-time records: after the first deploy, write `agent-identity-role` (and
   `existing-project-reuse` if an existing project was used), only for actions the user approved.
6. **Hand off to the Microsoft Foundry skill**: run its dependency check (needs `azd`), read its
   `azd-guidance`, then follow `deploy` → `invoke` → `observe`. It owns project/model provisioning
   (`azd provision`), so don't hardcode endpoints or secrets.
7. **Report** the tree, the resolved options, and the exact next command.

## Placeholders

`{{AGENT_NAME}}` `{{AGENT_DESCRIPTION}}` `{{MODEL}}` `{{MODEL_VERSION}}` `{{INSTRUCTIONS}}`
`{{CPU}}` `{{MEMORY}}` — all author-time, none may survive.

`FOUNDRY_PROJECT_ENDPOINT` is injected by the platform (hosted) / translated by azd (local). Never
declare it in `agent.yaml`. `AZURE_AI_MODEL_DEPLOYMENT_NAME` is the only declared env var.

## Constraints

* No secrets, endpoints, or subscription ids in generated files.
* Template is LangGraph-only. Another framework = new template, not a flag.
* No Dockerfile and no `agent.yaml`: current `azd` (1.37+) rejects both the legacy `agent.yaml` and a
  nested `config:` block. The whole agent is one service block in `azure.yaml`
  (`codeConfiguration` with `remote_build`), plus an `ai-project` service for the model.
* `azd init -t` only adopts into an empty dir; if `azure.yaml` is already rendered, skip it and run
  `azd env new <env>` + `azd env set AZURE_SUBSCRIPTION_ID / AZURE_TENANT_ID / AZURE_LOCATION /
  AZURE_RESOURCE_GROUP / AZURE_AI_PROJECT_ID / AZURE_AI_PROJECT_ENDPOINT /
  AZURE_AI_MODEL_DEPLOYMENT_NAME`. In Git Bash set `MSYS_NO_PATHCONV=1` or the ARM id
  `/subscriptions/...` gets rewritten to `C:/Program Files/Git/...`.
* **After the first deploy, grant the agent identity access.** Foundry creates a per-agent identity
  (`<account>-<project>-<agent>-AgentIdentity`) with no roles, so a deployed agent gets
  `401 ... lacks the required data action ...chat/completions/action` even though the local run
  works (local uses your credentials). Assign `Foundry User` to it at the Foundry account scope
  (ask the user first), wait a few minutes, then run a remote `azd ai agent invoke` to confirm.
* If `azd ai agent invoke` says the service "does not appear to have been deployed" (the agent was
  deployed outside this azd env), set `AGENT_<SERVICE_UPPER_SNAKE>_NAME` and `_VERSION` with
  `azd env set`; the agent list is at `<project endpoint>/agents?api-version=v1`.
* Never run `azd auth login` for the user; ask them to run `azd auth login` in their own terminal.
* Adopt it with `azd ai agent init -t ./azure.yaml --src . --agent-name <name>` (add
  `-p <project ARM id> -d <existing deployment>` to reuse a project; do not run `azd provision`).
* `agent.py`/`main.py` are modeled on Microsoft's `langgraph-chat-responses` hosted-agent sample, but
  are self-contained here: do not open or copy any sample project from the workspace. Generated code
  is unverified until it has been deployed and invoked once — say so in the report.
