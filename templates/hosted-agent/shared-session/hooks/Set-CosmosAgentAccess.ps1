# Grants the deployed hosted agent identity the built-in Cosmos DB data-plane role on THIS database only.
# Windows twin of postdeploy.sh. NOT run: azd was not available where this was written.
$ErrorActionPreference = 'Stop'

$values = @{}
azd env get-values | ForEach-Object { if ($_ -match '^([^=]+)="?(.*?)"?$') { $values[$Matches[1]] = $Matches[2] } }
$projectEndpoint = if ($values['AZURE_AI_PROJECT_ENDPOINT']) { $values['AZURE_AI_PROJECT_ENDPOINT'] } else { $values['FOUNDRY_PROJECT_ENDPOINT'] }
$agentName = ($values.Keys | Where-Object { $_ -match '^AGENT_.*_NAME$' } | Select-Object -First 1) | ForEach-Object { $values[$_] }
$accountId = $values['COSMOS_ACCOUNT_RESOURCE_ID']
$database = $values['AZURE_COSMOS_DATABASE_NAME']
if (-not $projectEndpoint -or -not $agentName -or -not $accountId -or -not $database) {
    throw 'Required project, agent, or Cosmos deployment values are missing.'
}

$foundryToken = az account get-access-token --resource 'https://ai.azure.com' --query accessToken --output tsv
$agent = Invoke-RestMethod -Headers @{ Authorization = "Bearer $foundryToken" } -Uri "$($projectEndpoint.TrimEnd('/'))/agents/$agentName`?api-version=v1"
$principalId = $agent.instance_identity.principal_id

$resourceGroup = ($accountId -split '/')[4]
$accountName = ($accountId -split '/')[-1]
$existing = az cosmosdb sql role assignment list --account-name $accountName --resource-group $resourceGroup `
    --query "[?principalId=='$principalId' && ends_with(scope, '/dbs/$database')].name" --output tsv
if ($existing) { Write-Host "Agent identity $principalId already has data access to $database."; return }

az cosmosdb sql role assignment create --account-name $accountName --resource-group $resourceGroup `
    --scope "/dbs/$database" --principal-id $principalId `
    --role-definition-id 00000000-0000-0000-0000-000000000002 | Out-Null
Write-Host "Granted Cosmos data-plane access on $database to agent identity $principalId."
