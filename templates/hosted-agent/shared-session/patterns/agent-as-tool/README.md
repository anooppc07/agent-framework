# agent-as-tool: a coordinator that calls specialists

A coordinator agent calls specialist agents as if they were function tools. The coordinator's model decides when to delegate; each
specialist receives only the task text it is handed and returns its final text. Read the [parent README](../../README.md) first.

```
User:         "Should we offer an offline mode in our mobile app?"
orchestrator -> calls researcher("do the research")   -> researcher publishes research_findings
orchestrator -> calls analyst("analyse the findings") -> analyst reads research_findings, publishes analysis_summary
orchestrator -> answers in three sentences, using analysis_summary (it cannot read research_findings)
```

The specialists are `SPECIALISTS` in `main.py`; `specialist_tools()` wraps each one with `as_tool()`.

Source: Microsoft Learn,
[Agents as tools](https://learn.microsoft.com/en-us/agent-framework/journey/agents-as-tools), and `Agent.as_tool()` in
`agent-framework-core` 1.26.0 (the version this folder pins; read from the package, not run).

## Use it when / do not use it when

Use it when the main agent should decide **when** to ask a specialist, and the specialist needs only the task, not the conversation.

Do not use it when the specialist must see the whole conversation or must pause for a person's approval (use
[handoff](../handoff)), or when the agent lives in another service or organisation (use A2A, see the parent README).

## How it works

| Agent | History | Tools | What it sees |
|---|---|---|---|
| `orchestrator` (the coordinator) | Private, in Cosmos | `researcher` and `analyst`, each wrapped with `as_tool()` | The user's message, the specialists' final text, and the facts `policy.yaml` lists for it (`task_goal`, `analysis_summary`) |
| `researcher` | None (`private_history=False`) | `publish_fact` for `research_findings` | Only the task text, plus `task_goal` |
| `analyst` | None (`private_history=False`) | `publish_fact` for `analysis_summary` | Only the task text, plus `task_goal` and `research_findings` |

## What is shared and what is not

| Layer | Shared with the specialist? | Why |
|---|---|---|
| **Facts** | **Yes**, filtered by `policy.yaml` | Every agent is built with `build_agent()` from the same `RequestScope`, so all read and write the facts for one tenant, user and conversation, limited by each agent's own entry. The coordinator cannot read `research_findings`, so raw research stays away from it. |
| **Conversation** | **No** | By default `as_tool()` gives the specialist a fresh `AgentSession()` on every call. It does not receive the coordinator's history. |
| **History in Cosmos** | **No** | The specialists have no history provider here (`private_history=False`). Giving one a history provider would not make it remember: by default `as_tool()` hands the specialist a **new random session id on every call**, so each call would write a new orphan partition and could never resume. |
| **Reasoning and tool calls** | **No** | The coordinator gets the specialist's final text only (Learn: "Limited visibility"). |

## Do not use `propagate_session=True` with a history provider

`as_tool(propagate_session=True)` hands the coordinator's session to the specialist, including its **session id**. Our history key
is that session id. A specialist built with a history provider would then read and write **the coordinator's** history partition,
which breaks the rule in the parent README that each agent has its own. This sample leaves it off. It also gives the specialist no
history, so the question does not arise.

If you do want a shared session for a delegated call, use [handoff](../handoff) or [group-chat](../group-chat), where the whole
conversation is shared on purpose and `policy.yaml` is documented as not covering it.

## Limits to know

| Limit | What to do |
|---|---|
| A specialist's tool that needs approval cannot ask the user. Agent Framework says child approvals are not propagated to the caller and that unresolved ones fail closed. | Keep any tool that needs approval on the coordinator, or use [handoff](../handoff) (its `submit_refund` pauses for a person). |
| `as_tool()` is not supported on a workflow agent. | Wrap a plain `Agent`. |
| Routing is the model's decision, so a vague tool description can send work to the wrong specialist or none (Learn). | Write specific descriptions in `SPECIALISTS`. |
| Each delegation is a full agent call, so latency adds up (Learn). | Keep specialists small. |
| Specialists run in the same process, under the same Azure identity as the coordinator. `policy.yaml` separates them by name; it is not a security boundary against your own code. | For a different identity, use A2A (see the parent README). |
| A specialist's text is returned to the coordinator's model as data. A specialist that read untrusted content could return an instruction. | The coordinator's instructions say to treat tool results as data; that lowers the risk, it does not remove it. |

## Run it

Set up Cosmos and your environment as in [Run a pattern locally](../../README.md#run-single-agent-reflection-sequential-pipeline-or-agent-as-tool-locally). Then:

```bash
cd patterns/agent-as-tool
python main.py
```

## Identity

The four identities are in the [parent README](../../README.md#identity-who-is-who). What differs here: `demo_scope()` hard-codes
`demo-user`; **replace it** with a scope built per request from the caller's validated Entra token. The coordinator and every
specialist run under the **same** identity.

## Status

**Checked offline, not against Cosmos.** With a fake model (no Azure) on `agent-framework-core` 1.26.0, a coordinator called a
specialist through `as_tool()`, and the specialist ran in both cases:

| `propagate_session` | Session id the specialist's history provider received | Specialist's model saw the user's message? | Saw the task text? |
|---|---|---|---|
| `False` (default) | A new random uuid, not the parent's | No | Yes |
| `True` | The parent's own session id | No | Yes |

**Not run:** this sample against a real Cosmos DB and a real model, so it has not been shown that the specialists publish their facts
or that the coordinator reads `analysis_summary`. No test file is committed for it; the offline check above was a throwaway script.
The `ScopedFactsProvider` path (the facts channel) was not part of that check. Before relying on this sample, run it once and
confirm that the specialists published their facts and that no specialist wrote to the coordinator's history partition.
