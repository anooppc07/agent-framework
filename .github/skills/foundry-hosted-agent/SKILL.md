---
name: foundry-hosted-agent
description: Scaffold a parameterized Azure AI Foundry hosted agent (kind: hosted, Responses protocol, Python + LangGraph) from a standard template, then hand provisioning, deploy, invoke, evals and troubleshooting to the Microsoft Foundry agent skill (azure-skills `microsoft-foundry`). Use when creating a new Foundry hosted agent (container/code agent), as opposed to a declarative prompt agent.
---

# Foundry Hosted Agent

Thin scaffold layer. This skill only generates the agent source from templates. Everything after that
(provision, deploy, invoke, observe, troubleshoot, CI/CD) belongs to the **Microsoft Foundry skill** (`microsoft-foundry` in the azure-skills pack; install it for Copilot) —
invoke that skill and follow its sub-skills; do not re-implement them here.

Hosted = your code runs in a Foundry-managed container. For a declarative agent with no code, use
`foundry-prompt-agent` instead.

## Inputs

Take any supplied as arguments; ask only for what is missing. Never invent a required value.

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
4. **Sweep**: `grep -rnE "\{\{[A-Z][A-Z0-9_]*\}\}" <output-dir>` must return nothing
   (`{{item.*}}` / `{{sample.*}}` in `evals/` are Foundry templates, not placeholders).
5. **Hand off to the Microsoft Foundry skill**: run its dependency check (needs `azd`), read its
   `azd-guidance`, then follow `deploy` → `invoke` → `observe`. It owns project/model provisioning
   (`azd provision`), so don't hardcode endpoints or secrets.
6. **Report** the tree, the resolved options, and the exact next command.

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
* `agent.py`/`main.py` follow the `langgraph-chat-responses` sample in
  `pilot/assert-src/examples/langgraph-foundry-hosted`. Generated code is unverified until it has
  been deployed and invoked once — say so in the report.
