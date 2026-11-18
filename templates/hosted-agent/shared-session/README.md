# Shared sessions on Cosmos DB

Agent Framework patterns where several agents work on one conversation, with every piece of state in a **Cosmos DB the
PortCo owns** and every model call through the **AI Gateway**. Two patterns are hosted templates (they deploy as a Foundry
hosted agent). Three are library samples (you run them in a script, a job or your own API).

Read [`../../_gateway/GATEWAY-CONTRACT.md`](../../_gateway/GATEWAY-CONTRACT.md) first for the gateway rules (R1-R10).

## Choosing a pattern

| I need... | Pattern | Kind | Shares a session? |
|---|---|---|---|
| One conversation that moves between specialists (triage, then billing or tech support) | [`handoff`](patterns/handoff) | **Hosted** | Yes: the live conversation |
| Several agents discussing in one room until a round limit | [`group-chat`](patterns/group-chat) | **Hosted** | Yes: one shared room transcript |
| One agent that remembers a user across restarts | [`single-agent`](patterns/single-agent) | Library sample | No |
| A writer and a critic that iterate on a draft | [`reflection`](patterns/reflection) | Library sample | No: the critic sees only the draft |
| A fixed chain where each stage passes a small result on | [`sequential-pipeline`](patterns/sequential-pipeline) | Library sample | No: stages pass policy-filtered facts |

Rule of thumb: share a session only when the user experience needs one continuous conversation (handoff, group chat).
Everywhere else, pass artifacts or facts.

Not here yet: patterns that have not been run end to end against a real Cosmos DB and model. They are added when they are.

## The three layers of state

| Layer | Cosmos container (partition key) | Who can read it |
|---|---|---|
| **Shared facts** | `shared-facts` (`/scope`) | Only agents allowed by `config/policy.yaml` (deny by default, with a classification cap) |
| **Private history** | `chat-history` (`/session_id`) | Only that agent (library samples; the hosted templates keep the conversation in the workflow instead) |
| **Workflow state** | `workflow-checkpoints` (`/workflow_name`) | The orchestration; this is where a hosted handoff or group chat keeps its conversation |

Every partition key is a hash of tenant, trusted user and conversation (and the agent, for history). **No raw user,
tenant or conversation id is written to Cosmos.** Verified by searching all three containers after real runs.

> **What separates the agents.** Each agent's access is its entry in `config/policy.yaml`, applied when it runs through
> `ScopedFactsProvider`: the model's `publish_fact` tool has the agent's name fixed when the agent is built, so the model
> cannot publish as another agent. All agents in a template still run under **one** Azure identity, and `SharedFactStore.publish`
> takes the agent name as an argument, so code in the same process can publish as any agent. The policy separates agents; it is not
> a boundary against your own code.
>
> **What `policy.yaml` does and does not cover.** It filters the shared **facts** channel. It does **not** filter a live
> handoff or group-chat conversation: anything said there (the user's raw messages, another agent's reply, a refund reason
> as text) is visible to every participant. If one agent must never see what another discusses, do not put them in the
> same handoff or room. Group chat refuses a room that mixes clearance levels; handoff has no such check.

## Identity: who is who

Four separate identities are involved. They are easy to confuse, so each pattern README repeats this table for its own case.

| # | Identity | Who it is | Where it comes from | What it is used for |
|---|---|---|---|---|
| 1 | **The end user** | The person calling the agent | The Foundry host sets it from the platform (`scope.user_id`). In a hosted deployment the host **refuses the request** if the user id or call id is missing, and rejects a session id that does not match the platform's. **Never read from the request body.** | Partitioning facts and checkpoints, so one user cannot read another's |
| 2 | **The agent's own identity** | The deployed agent's managed identity (`instance_identity.principal_id`) | Created by the platform at deploy time | Cosmos data-plane access. `hooks/postdeploy.sh` grants the built-in *Cosmos DB Built-in Data Contributor* role on **this one database only** |
| 3 | **Model access** | The same agent identity | `gateway_client.chat_client()` fetches a Microsoft Entra token for `GATEWAY_TOKEN_SCOPE` and sends it to the AI Gateway | Model calls. The gateway holds model and tool credentials; the agent never does |
| 4 | **The tenant** | The company (PortCo) | The `TENANT_ID` setting: one hosted agent per company | Hashed into every partition. **Not a security boundary on its own**: the trusted user is |

How the credential is chosen:

| Where it runs | Cosmos credential | Model-token credential |
|---|---|---|
| Deployed (`APP_ENV` is not `dev`, `test` or `local`) | `ManagedIdentityCredential` (the agent's identity; set `AZURE_CLIENT_ID` only for a user-assigned one) | `DefaultAzureCredential` in `gateway_client.py` (see below) |
| Local (`APP_ENV` is `dev`, `test` or `local`) | `DefaultAzureCredential`, which uses `az login` | `DefaultAzureCredential` |

**Known gap against Azure guidance.** Azure recommends `ManagedIdentityCredential` in production and
`DefaultAzureCredential` for local development only. The Cosmos client follows that. `gateway_client.py` is the repo's
shared copy, used by every hosted template, and still uses `DefaultAzureCredential`. In a deployed container it should
find the managed identity, but that has not been run. Changing the shared file is a decision for the owners of
`_gateway/`; these templates do not fork it.

Roles by person:

| Who | Role | Where |
|---|---|---|
| Developers | `Azure AI User` | The **dev** Foundry project only (KB 08) |
| Humans in prod | None. Break-glass through PIM | Rule 4 |
| The deployed agent | *Cosmos DB Built-in Data Contributor* | The one Cosmos database, granted by `hooks/postdeploy.sh` |
| The deployed agent | Whatever the AI Gateway requires for `GATEWAY_TOKEN_SCOPE` | **DataX: not in the KB** |

## Layout

```
shared-session/
  README.md                 this file
  patterns/                 one folder per pattern, each with its own README and runnable code
    handoff/                  hosted template
    group-chat/               hosted template
    single-agent/             library sample
    reflection/               library sample
    sequential-pipeline/      library sample
  common/                   the shared modules, policy and gateway client (edit here only)
  infra/cosmos/             Terraform: the account, database, three containers, private endpoint
  hooks/                    post-deploy: grants the agent identity its Cosmos role (sh and PowerShell)
  sync-common.sh            copies common/ into every pattern; `sh sync-common.sh --check` fails on drift
```

Each pattern folder contains its own copy of `common/` (`app/`, `config/`, `gateway_client.py`) so it deploys on its own, the
same convention as `_gateway/gateway_client.py`. Edit `common/`, then run `sh sync-common.sh`. To add a pattern, create
`patterns/<name>/`, add its `src/agent` (or the folder itself, for a sample) to the `for target in` list in `sync-common.sh`, and
add a row to the table at the top of this file.

## Azure setup

The Terraform in `infra/cosmos/` (`main.tf`, `variables.tf`, `outputs.tf`, `main.tfvars.json`) provisions the account, the
database and the three containers. **The app never creates a database or a container.** With Entra-only data-plane access a
create call is refused (HTTP 403, verified), so a missing container shows up as a plain 404 on first use.

| Container | Partition key | Container TTL (default) | Holds |
|---|---|---|---|
| `chat-history` | `/session_id` | 30 days | Raw user text per agent (library samples) |
| `shared-facts` | `/scope` | 1 day | Redacted facts |
| `workflow-checkpoints` | `/workflow_name` | 7 days | The whole conversation per checkpoint, raw |

The Terraform turns key auth off (`local_authentication_enabled = false`), disables public network access, and creates a private
endpoint. The private endpoint subnet and the private DNS zone `privatelink.documents.azure.com` come from the platform stamp.
**Container TTL must be on** for any container whose items should expire: an item-level `ttl` is ignored when the container's
TTL is off (verified).

**Inputs.** `azd` fills `infra/cosmos/main.tfvars.json` from its environment (`${VAR}` substitution). Set these before `azd up`:

| azd environment value | Terraform variable | Notes |
|---|---|---|
| `AZURE_LOCATION` | `location` | Set by `azd` |
| `AZURE_RESOURCE_GROUP` | `resource_group_name` | An **existing** group; this module does not create one. Whether the Foundry layer exports this name was not checked: set it yourself if it does not |
| `COSMOS_ACCOUNT_NAME` | `account_name` | Globally unique, 3-44 characters, lowercase letters, digits and hyphens |
| `PRIVATE_ENDPOINT_SUBNET_ID` | `private_endpoint_subnet_id` | From the platform stamp |
| `COSMOS_PRIVATE_DNS_ZONE_ID` | `private_dns_zone_id` | From the platform stamp |

Retention, serverless and the database name are Terraform variables with defaults (`fact_ttl_seconds`, `history_ttl_seconds`,
`checkpoint_ttl_seconds`, `serverless`, `database_name`); set them with `azd env set TF_VAR_<name> <value>`.

**Outputs.** Upper-case Terraform outputs become `azd` environment values: `AZURE_COSMOS_ENDPOINT`, `AZURE_COSMOS_DATABASE_NAME`
and `COSMOS_ACCOUNT_RESOURCE_ID`. `azure.yaml` and the post-deploy hooks read exactly these names.

## Run a library sample locally

You need a Cosmos database with the three containers already created (apply `infra/cosmos`, or create them by hand with the
partition keys and TTLs in the table above), and your own user granted *Cosmos DB Built-in Data Contributor* on that database.

```bash
cd patterns/single-agent                          # or reflection, sequential-pipeline
cp .env.example .env                              # fill in the placeholders; set APP_ENV=dev for a local run
python -m venv .venv && . .venv/bin/activate      # on Windows: .venv\Scripts\activate
pip install --pre -r requirements.txt
az login
python main.py
```

Packages come from the DataX feed (PyPI is blocked at the firewall); point `pip` or `uv` at it with its index setting.

## What was and was not run

Every pattern here was run against a real Cosmos DB account (serverless, Entra-only) and a real `gpt-5-mini` deployment, using
the repo's `gateway_client` pointed at an OpenAI-compatible endpoint.

**Run:** both hosted templates through the real `ResponsesHostServer` (in-process over ASGI, a new server instance for each
turn to model a restart); the three samples as shipped; user isolation (a second user sending the first user's conversation id
reads nothing); blank input as a first message and in the middle of a conversation; the refund tool running exactly once across
an approval after a restart; no raw user, tenant or conversation id stored in any container.

**Not run:**
- Anything deployed: `azd up`, the post-deploy hook, a real Foundry hosted-agent identity. **The Terraform has never been
  validated, planned or applied** (the Terraform CLI was not available): its argument names come from the `azurerm` provider
  documentation, and a static check confirmed only that its variables, outputs and resource references are consistent. The
  databases used in the tests above were created by hand with `az`, using the same partition keys and TTLs. The hooks have
  **never been run**. Run `terraform init && terraform validate` before anything else.
- The AI Gateway itself. The model was called directly on a Foundry account; the gateway's URL shapes and token audience are
  not in the KB (DataX supplies them).
- `ManagedIdentityCredential` against Cosmos. The credential choice was checked; a real managed identity was not.
- A process killed in the middle of a model call. Restarts were clean exits between turns.
- Streaming responses, load, request-unit cost, private endpoints.
- A model that stays inside its instructions under attack. One injected instruction in a fact was tried three times and did not
  take effect; that is not a security test.

## Behaviour to be aware of

1. **A failed turn blocks its conversation for good.** The host will not replay a turn that may have already run a tool such as
   `submit_refund`. After a failure, every later request in that conversation returns *"This workflow turn was interrupted or
   failed; start a fresh workflow lineage"*. Tell the user to start a new conversation. Do not try to carry state across: a
   recovery hint passed in the request `metadata` made the model call fail with *"The 'metadata' parameter is only allowed when
   'store' is enabled"*, so it is not included.
2. **The workflow graph must be identical on every turn of a conversation.** The host stores a hash of it and refuses a different one
   (*"The stored workflow graph is incompatible"*, and the conversation is dead for good). Never return a different workflow for a
   special case such as blank input; handle it inside the one workflow. Found when a separate blank-input workflow broke conversations.
3. **The host logs only the exception type** for a failed turn. Every agent here logs a full traceback (`app/diagnostics.py`).
4. **Checkpoints hold the whole conversation, raw.** A card number typed by the user is stored in `workflow-checkpoints`
   unredacted, and two group-chat rooms produced about 2 MB of checkpoints in 38 documents. Redaction runs only on facts. Set
   the checkpoint TTL to your retention rule, and add input redaction before real data.
5. **Checkpoints are pickled.** Anyone who can write to the container can run code in the agent. Only the agent's identity
   should have write access. One extra type, `types:GenericAlias`, is allow-listed, because the handoff "waiting for the user"
   pause carries it; without it the pause is silently **not** saved and a restart resumes from before the pause.
6. **The host stores the user id in each checkpoint.** It writes it under `state._workflow_run_kwargs...user_id`.
   `ScopedCheckpointStorage` removes it before saving, and the host re-injects the current trusted identity on every turn.
7. **Concurrent saves of one message can duplicate it.** Only matters if two requests reach one conversation at the same time.
8. **Set `SCOPE_HASH_PEPPER`.** Outside `dev`, `test` and `local` the agent refuses to start without a 32+ character value.
   Changing it later orphans everything stored.
9. **A group-chat round limit is a budget for the whole conversation**, not per message: the first message uses it and later messages
   get one reply each. See the [group-chat README](patterns/group-chat/README.md#follow-up-messages-a-room-is-effectively-single-turn).

## Open decisions (defaults chosen; change them before real data)

| Decision | Default | Change it if |
|---|---|---|
| Where the tenant comes from | `TENANT_ID` setting: one hosted agent per company | One host serves several companies: resolve it per request from a trusted claim |
| Shared handoff or room transcript | Accepted | Billing details must stay out of the technical agent's context: use separate services |
| Pickle allow-list | One extra type, `types:GenericAlias` | Your security review forbids widening it: swap in a JSON-only checkpoint store |
| Retention | History 30 d, checkpoints 7 d, facts 1 d | Your retention policy says otherwise |
| PII in checkpoints and history | Stored raw, TTL-limited | You must not hold raw PII: redact input before it reaches the agent |

## Exception to sign off (DataX)

KB 01 lists Cosmos DB as Foundry landing-zone state, so this is within the baseline. What is **not** signed off is this template's
specific use: checkpoints with the one allow-listed type, raw conversation text stored with a TTL, and an agent identity with
data-plane write on a PortCo-owned database. Confirm all three before real data.
