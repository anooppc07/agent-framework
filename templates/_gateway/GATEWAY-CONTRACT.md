# Gateway contract

What every template assumes the SCBX AI Gateway (APIM) provides. The architecture KB does not give
URL shapes or token audiences, so those are **placeholders DataX must fill**. Nothing here is invented
as fact; each row says where the rule comes from.

## The rules the templates enforce

| # | Rule | Source (`docs/architecture-kb/`) |
|---|---|---|
| R1 | An agent calls models only through the gateway **Models API**, using one logical model name. | 02 flow 1, "Model access detail" |
| R2 | A prompt agent uses an *admin-connected* model, `ai-gateway/<model>`. | 02, 01 (Models) |
| R3 | A hosted agent calls the gateway **from code** with a Microsoft Entra token. | 02 "Model access detail" |
| R4 | An agent reaches tools and MCP servers only through the gateway **Tools and MCP API**. | 02 flow 3 |
| R5 | Tool credentials are held by the gateway and never reach the agent. | 02 "Tool call detail" |
| R6 | MCP servers are registered in API Center, fronted by APIM, and surfaced in Foundry; agents opt in per tool. | 07 "MCP lifecycle" |
| R7 | Prompts and responses stay in the PortCo. Content recording is off in prod. | 08 "Keep PII out of logs" |
| R8 | One correlation ID (`traceparent`) end to end. | 02 flow 5, cross-PortCo rule 5 |
| R9 | Private by default: public access off, private endpoints, egress through the hub firewall allow-list. | README rule 2, 08 |
| R10 | Prod has no human access; only the shared DataX pipeline writes to Foundry. Templates deploy to **dev**. | README rule 4, 07 |

R1 and R4 are enforced by the platform (private endpoints, firewall allow-list, Azure Policy approved-model list).
A template cannot enforce them. It can only avoid creating a bypass, and the lint checks that.

## Environment variables (hosted agents)

| Variable | Meaning | Who provides it |
|---|---|---|
| `GATEWAY_MODELS_BASE_URL` | OpenAI-compatible base URL of the Models API for this PortCo's gateway | **DataX: not in the KB** |
| `GATEWAY_MODEL_NAME` | Logical model name (not a deployment name) | DataX model registry |
| `GATEWAY_MCP_BASE_URL` | Base URL of the Tools and MCP API; an MCP server is `<base>/<server-path>` | **DataX: not in the KB** |
| `GATEWAY_TOKEN_SCOPE` | Entra scope the gateway validates, e.g. `api://<gateway-app-id>/.default` | **DataX: not in the KB** |
| `GATEWAY_TOKEN_AUDIENCE` | Same app as the scope without `/.default`, e.g. `api://<gateway-app-id>`; used by Foundry connections | **DataX: not in the KB** |

## Prompt agents

| Setting | Value |
|---|---|
| `model:` | `ai-gateway/<logical-model>`. Foundry routes via the account's APIM connection with the project managed identity. |
| MCP tool `server_url` | The APIM MCP endpoint from the gateway; never a vendor URL. `project_connection_id` names a `RemoteTool` connection that points at the gateway. |
| `require_approval` | `always` for any server with a write tool, `never` only for servers the registry marks read-only. |

## Known exceptions (need DataX sign-off)

| Template | Exception | Why it is allowed | What to confirm |
|---|---|---|---|
| `hosted-agent/foundry-memory` | The memory store calls a chat and an embedding deployment inside the Foundry account | 01: "workload-specific" models are called directly inside the account | Both deployments are on the approved-model list |
| `hosted-agent/custom-store` | Uses Azure Managed Redis | The KB lists Cosmos DB, AI Search and Storage as Foundry-LZ state; Redis appears only in the AI Gateway LZ and Auto Ops | DataX approves Redis in the Foundry LZ, or swaps the four classes in `stores.py` for Cosmos DB |
| `prompt-agent/toolbox` | Uses the preview `microsoft_copilot_preview` harness | Upstream requires it for toolboxes | Harness is allowed in the SCBX region (KB 07 lists toolboxes as a PoC item) |

## Not covered

Content Safety and PII redaction are gateway policies (08), not agent code, so no template sets them.
Cross-PortCo calls (gateway to gateway) are out of scope. Evals, pipelines and registration in API Center
are out of scope for this first cut.
