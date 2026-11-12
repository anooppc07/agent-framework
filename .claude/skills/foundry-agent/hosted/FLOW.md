# Foundry Hosted Agent (flow)

> Runs after the router's Q0 chose **hosted** and the shared questions are answered (`../SKILL.md`).
> Paths below (`assets/`) are relative to this `hosted/` folder.

Thin scaffold layer. This skill only generates the agent source from templates and runs local checks.
**Creation never deploys**: it does not provision, deploy, install a workflow, dispatch a pipeline or call
Azure. Provisioning, deployment, invocation and observation are a separate, explicit step the user starts
afterwards (the `azure:microsoft-foundry` skill owns it); do not re-implement or start it here.

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

## One creator per pattern

Exactly one path creates each agent; never run two.

* **`router-specialists`**: created by the intake/scaffold CLIs, never by copying files by hand. From the
  skill root run `python overlay/intake.py questions|build|create` (see `../SKILL.md`, "Fast path"), or
  `python overlay/scaffold_overlay.py --spec <spec> --out <dir> --mode create-new`. It writes a complete
  MAF package (`agent.py`, `main.py`, `specialists.json`, `requirements.txt`, `preflight.py`,
  `azure.yaml`, `.agentignore`, the spec, scaffold metadata and the runtime contract). Do not copy
  `patterns/router-specialists/` yourself.
* **`single-agent`**: created by the steps below.
* **`supervisor-worker`, `agent-handoffs`**: design-only proposals (`--mode design-only`); nothing runnable.
* An **existing** agent only gets a contract (`--mode overlay-existing`); its code, framework and session
  configuration are never changed or relabelled as MAF.

## Flow (single-agent)

1. **Resolve inputs** (table above). Confirm `name`, `output-dir`, `instructions` before writing.
2. **Copy `assets/` into `output-dir`**, substituting every `{{PLACEHOLDER}}` (list below).
   Substitute `{{INSTRUCTIONS}}` only in `instructions.md` (read at runtime by `agent.py`). If
   `tools=none`, remove the `echo` tool between the `sample-tool` markers in `agent.py` and leave
   `_tools = []`; if `sample`, keep it.
3. **Evals** (`evals=yes`, default): copy `assets/evals/` to `<output-dir>/evals/`, replace
   `{{AGENT_NAME}}`, and keep `evaluators/tools.py` `TOOL_DEFINITIONS` plus every dataset row's
   `tool_definitions` in sync with the tools in `agent.py` (the `name` must equal the function
   name, or `tool_call_accuracy` returns "Not applicable"). Rewrite the dataset rows for the real
   agent. Install `evals/requirements.txt` + `pytest` in a throwaway venv and run
   `python -m pytest tests/ -v` from `evals/`; all must pass. For a tool-less agent
   follow "Agent without tools" in `evals/README.md`.
4. **Decision records.** Follow "Decision records" in `../SKILL.md`: `agent-type`, `model-runtime`,
   `data-access`, `behavior-boundaries`, `evaluation`, `container-sizing`. Skip `pipeline`. Add
   `docs/` to the agent's `.agentignore` so the records are not zipped into the deploy.
5. **Session check**: run `python preflight.py` in `output-dir`. Exit 0 (`enabled`) is required; `disabled` or
   `unknown` means the host was changed in a way the check cannot vouch for. It reads source only.
6. **Sweep**: `grep -rnE "\{\{[A-Z][A-Z0-9_]*\}\}" <output-dir>` must return nothing
   (`{{item.*}}` / `{{sample.*}}` in `evals/` are Foundry templates, not placeholders).
7. **Report** the tree, the resolved options and the local checks that passed. Say plainly that nothing
   was deployed and that hosted behavior (managed-session enablement, multi-turn continuity, restart
   persistence, isolation) is unverified until a separately authorized deployment is checked.

## MAF, sessions and invocation

The template is Microsoft Agent Framework (MAF) only: another framework is a new template, not a flag.
Foundry-managed sessions are required and come from the standard `ResponsesHostServer` default; there is
no enable flag and no custom session, history, checkpoint or protocol code to write. Never pass a storage
override: `preflight.py` fails if you do. Semantic memory, business-case state and tenant authorization are
separate decisions the template does not make.

## Placeholders

`{{AGENT_NAME}}` `{{AGENT_DESCRIPTION}}` `{{MODEL}}` `{{MODEL_VERSION}}` `{{INSTRUCTIONS}}`
`{{CPU}}` `{{MEMORY}}` — all author-time, none may survive.

`FOUNDRY_PROJECT_ENDPOINT` is injected by the platform (hosted) / translated by azd (local). Never
declare it in `agent.yaml`. `AZURE_AI_MODEL_DEPLOYMENT_NAME` is the only declared env var.

## Constraints

* No secrets, endpoints, or subscription ids in generated files.
* Local files and local checks only. Never run `azd`, `az` deployment commands or any workflow during creation.
* No Dockerfile and no `agent.yaml`: current `azd` (1.37+) rejects both the legacy `agent.yaml` and a
  nested `config:` block. The whole agent is one service block in `azure.yaml`
  (`codeConfiguration` with `remote_build`), plus an `ai-project` service for the model. The protocol is
  `responses` 2.0.0 for the pinned hosting package.
* `agent.py`/`main.py` are modeled on Microsoft's `responses/basic` Agent Framework hosted-agent sample, but
  are self-contained here: do not open or copy any sample project from the workspace. Generated code
  is unverified until it has been deployed and invoked once; say so in the report.

## Notes for the separate deploy step (not part of creation)

Recorded so the knowledge is not lost. None of this is run, installed or asked for during creation.

* `azd init -t` only adopts into an empty dir; if `azure.yaml` is already rendered, skip it and set the
  azd environment values instead. In Git Bash set `MSYS_NO_PATHCONV=1` or ARM ids get rewritten.
* After a first deployment Foundry creates a per-agent identity with no roles, so a deployed agent can fail
  with `401 ... lacks the required data action` even though a local run works. Granting it access needs the
  user's approval at that time.
* If `azd ai agent invoke` says the service "does not appear to have been deployed", the agent was deployed
  outside this azd env; set the agent name and version with `azd env set`.
* Never run `azd auth login` for the user; ask them to.
