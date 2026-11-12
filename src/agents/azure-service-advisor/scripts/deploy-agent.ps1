<#
.SYNOPSIS
    Deploys the "azure-service-advisor" declarative Foundry prompt agent.

.DESCRIPTION
    Renders the prompt-agent definition (injecting instructions.md and substituting the model
    deployment and MCP server URL) and publishes it to the target Foundry
    project via the Agents data-plane REST API:

        POST {project}/agents?api-version=v1                    (first deploy)
        POST {project}/agents/{name}/versions?api-version=v1    (subsequent deploys)

    Prompt agents are declarative, so there is no container build and no image push. `azd ai agent`
    only covers *hosted* agents, which is why this uses `az rest` against the project endpoint
    (`--resource https://ai.azure.com` selects the right audience).

    Foundry agents are versioned, so re-deploying publishes a new version — the one the frontend's
    `agent_reference` (no explicit version) resolves to. Pass -WhatIf to render and print the payload
    without calling Azure.

.PARAMETER ProjectEndpoint
    Foundry project endpoint. Defaults to $env:FOUNDRY_PROJECT_ENDPOINT. Required.

.PARAMETER ModelDeploymentName
    Model deployment name in the Foundry project. Defaults to
    $env:AZURE_AI_MODEL_DEPLOYMENT_NAME. Required.

.PARAMETER AgentName
    Agent name in the project. Defaults to "azure-service-advisor". A frontend sends this as
    `agent_reference.name`, so changing it must stay in sync with the consumer.

.PARAMETER McpServerUrl
    (grounding=mcp) The remote MCP server URL. Defaults to $env:MCP_SERVER_URL.

.PARAMETER WhatIf
    Render and print the payload, then exit without calling Azure.

.EXAMPLE
    ./deploy-agent.ps1 `
      -ProjectEndpoint "https://<res>.services.ai.azure.com/api/projects/<proj>" `
      -ModelDeploymentName "<model-deployment>" `
      -McpServerUrl "<remote-mcp-server-url>"
#>
[CmdletBinding()]
param(
    [string]$ProjectEndpoint = $env:FOUNDRY_PROJECT_ENDPOINT,
    [string]$ModelDeploymentName = $env:AZURE_AI_MODEL_DEPLOYMENT_NAME,
    [string]$AgentName = 'azure-service-advisor',
    [string]$McpServerUrl = $env:MCP_SERVER_URL,
    [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'

$apiVersion = 'v1'
$audience = 'https://ai.azure.com'

if ([string]::IsNullOrWhiteSpace($ProjectEndpoint)) {
    throw "ProjectEndpoint is required (pass -ProjectEndpoint or set FOUNDRY_PROJECT_ENDPOINT)."
}
if ([string]::IsNullOrWhiteSpace($ModelDeploymentName)) {
    throw "ModelDeploymentName is required (pass -ModelDeploymentName or set AZURE_AI_MODEL_DEPLOYMENT_NAME)."
}
if ([string]::IsNullOrWhiteSpace($McpServerUrl)) {
    throw "McpServerUrl is required (pass -McpServerUrl or set MCP_SERVER_URL)."
}

$scriptDir = $PSScriptRoot
$agentRoot = Split-Path -Parent $scriptDir
$definitionPath = Join-Path $agentRoot 'azure-service-advisor.agent.json'
$instructionsPath = Join-Path $agentRoot 'instructions.md'
foreach ($path in @($definitionPath, $instructionsPath)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Required file not found: $path" }
}

$baseUrl = $ProjectEndpoint.TrimEnd('/')

Write-Host "Project endpoint : $baseUrl"
Write-Host "Agent name       : $AgentName"
Write-Host "Model deployment : $ModelDeploymentName"

# Deploy-time placeholder substitutions. Only values that are set are replaced; the final
# placeholder check below fails the deploy if any required placeholder was left unsubstituted.
$substitutions = @{
    '__AZURE_AI_MODEL_DEPLOYMENT_NAME__' = $ModelDeploymentName
    '__MCP_SERVER_URL__'                 = $McpServerUrl
}

$rendered = Get-Content -Raw -LiteralPath $definitionPath
foreach ($key in $substitutions.Keys) {
    $value = $substitutions[$key]
    if (-not [string]::IsNullOrWhiteSpace($value)) {
        $rendered = $rendered.Replace($key, $value)
    }
}

$agent = $rendered | ConvertFrom-Json
$agent.name = $AgentName
$instructions = Get-Content -Raw -LiteralPath $instructionsPath
$instructions = [regex]::Replace(
    $instructions,
    '\A---\r?\n.*?\r?\n---\r?\n',
    '',
    [System.Text.RegularExpressions.RegexOptions]::Singleline
)
$agent.definition.instructions = $instructions.Trim()

# $-prefixed keys are file-level documentation, not part of the API contract.
$agent.PSObject.Properties.Name |
    Where-Object { $_.StartsWith('$') } |
    ForEach-Object { $agent.PSObject.Properties.Remove($_) }

$createBody = $agent | ConvertTo-Json -Depth 20
$versionBody = @{ definition = $agent.definition } | ConvertTo-Json -Depth 20

if ($createBody -match '__[A-Z_]+__') {
    throw "Rendered definition still contains unsubstituted placeholders. Provide the matching parameter/env var for your grounding type."
}

if ($WhatIf) {
    Write-Host ""
    Write-Host "-WhatIf: rendered create payload (not sent):"
    Write-Host $createBody
    return
}

$bodyFile = New-TemporaryFile
try {
    # az writes diagnostics to stderr; under ErrorActionPreference='Stop' PowerShell turns native
    # stderr into a terminating error, so relax it here and gate on $LASTEXITCODE instead.
    $ErrorActionPreference = 'Continue'

    $existing = az rest --method GET --url "$baseUrl/agents/$AgentName`?api-version=$apiVersion" --resource $audience 2>$null
    $exists = ($LASTEXITCODE -eq 0)

    # Foundry rejects publishing a version whose kind differs from the existing agent's
    # ("Agent kind mismatch"). When the kinds differ we must delete and recreate, not version.
    $existingKind = $null
    if ($exists) {
        $existingKind = ($existing | ConvertFrom-Json).versions.latest.definition.kind
    }
    $newKind = $agent.definition.kind

    if ($exists -and $existingKind -ne $newKind) {
        Write-Host "Agent '$AgentName' exists as kind '$existingKind' but this deploy is kind '$newKind' — deleting and recreating..."
        az rest --method DELETE --url "$baseUrl/agents/$AgentName`?api-version=$apiVersion&force=true" --resource $audience | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Failed to delete existing '$AgentName' (kind '$existingKind') for the kind cutover." }
        $exists = $false
    }

    if ($exists) {
        Write-Host "Agent '$AgentName' exists — publishing a new version..."
        $url = "$baseUrl/agents/$AgentName/versions`?api-version=$apiVersion"
        Set-Content -LiteralPath $bodyFile -Value $versionBody -Encoding utf8
    }
    else {
        Write-Host "Creating agent '$AgentName'..."
        $url = "$baseUrl/agents`?api-version=$apiVersion"
        Set-Content -LiteralPath $bodyFile -Value $createBody -Encoding utf8
    }

    $result = az rest --method POST --url $url --resource $audience `
        --headers "Content-Type=application/json" --body "@$bodyFile"
    if ($LASTEXITCODE -ne 0) { throw "az rest POST $url failed (exit $LASTEXITCODE)." }

    $parsed = $result | ConvertFrom-Json
    $version = if ($parsed.PSObject.Properties.Name -contains 'version') { $parsed.version }
    else { $parsed.versions.latest.version }
    Write-Host "Published '$AgentName' version $version."

    Write-Host ""
    Write-Host "Consumer Responses endpoint: $baseUrl/openai/v1/responses"
}
finally {
    Remove-Item -LiteralPath $bodyFile -ErrorAction SilentlyContinue
}
