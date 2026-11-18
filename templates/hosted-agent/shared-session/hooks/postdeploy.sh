#!/bin/sh
# Grants the deployed hosted agent identity the built-in Cosmos DB data-plane role on THIS database only.
# Adapted from custom-store/hooks/postdeploy.sh. NOT run: azd was not available where this was written.
# The same role-assignment call was run by hand against a throwaway database (az cosmosdb sql role assignment create).

set -eu

main() {
  azd_values="$(azd env get-values)"
  project_endpoint="$(printf '%s\n' "${azd_values}" |
    sed -n 's/^\(AZURE_AI_PROJECT_ENDPOINT\|FOUNDRY_PROJECT_ENDPOINT\)="\(.*\)"$/\2/p' | head -n 1)"
  agent_name="$(printf '%s\n' "${azd_values}" | sed -n 's/^AGENT_.*_NAME="\(.*\)"$/\1/p' | head -n 1)"
  account_id="$(printf '%s\n' "${azd_values}" | sed -n 's/^COSMOS_ACCOUNT_RESOURCE_ID="\(.*\)"$/\1/p')"
  database="$(printf '%s\n' "${azd_values}" | sed -n 's/^AZURE_COSMOS_DATABASE_NAME="\(.*\)"$/\1/p')"

  if [ -z "${project_endpoint}" ] || [ -z "${agent_name}" ] || [ -z "${account_id}" ] || [ -z "${database}" ]; then
    echo "ERROR: Required project, agent, or Cosmos deployment values are missing." >&2
    exit 1
  fi

  # 1. The deployed agent's identity.
  foundry_token="$(az account get-access-token --resource 'https://ai.azure.com' --query accessToken --output tsv)"
  principal_id="$(curl --fail --silent --show-error \
    --header "Authorization: Bearer ${foundry_token}" \
    "${project_endpoint%/}/agents/${agent_name}?api-version=v1" |
    python3 -c "import json, sys; print(json.load(sys.stdin)['instance_identity']['principal_id'])")"

  # 2. Built-in "Cosmos DB Built-in Data Contributor" (00000000-0000-0000-0000-000000000002), scoped to the database.
  resource_group="$(printf '%s' "${account_id}" | sed -n 's|.*/resourceGroups/\([^/]*\)/.*|\1|p')"
  account_name="${account_id##*/}"
  existing="$(az cosmosdb sql role assignment list --account-name "${account_name}" --resource-group "${resource_group}" \
    --query "[?principalId=='${principal_id}' && ends_with(scope, '/dbs/${database}')].name" --output tsv)"
  if [ -n "${existing}" ]; then
    echo "Agent identity ${principal_id} already has data access to ${database}."
    exit 0
  fi
  az cosmosdb sql role assignment create \
    --account-name "${account_name}" --resource-group "${resource_group}" \
    --scope "/dbs/${database}" --principal-id "${principal_id}" \
    --role-definition-id 00000000-0000-0000-0000-000000000002 >/dev/null

  echo "Granted Cosmos data-plane access on ${database} to agent identity ${principal_id}."
}

main "$@"
