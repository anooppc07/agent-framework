<#
.SYNOPSIS
  Rehearse the card-dispute demo end to end, locally, against your real model. Deploys nothing.

.DESCRIPTION
  Does what the skill does, without the chat: builds a spec from the card-dispute preset, creates the
  agent folder (only with the explicit confirmation flags), runs the session preflight, and then runs
  the generated router against the real model.

  Needs: `az login` done, and FOUNDRY_PROJECT_ENDPOINT either set in the environment or in a .env file
  next to this script (that file is yours; it is never read from or written to the repo by the skill).

.EXAMPLE
  .\run-demo.ps1 -Owner payments-team
  .\run-demo.ps1 -Owner payments-team -Classification confidential -Name my-dispute-agent
  .\run-demo.ps1 -Owner payments-team -Fake        # rehearsal without the network
#>
param(
  [Parameter(Mandatory = $true)][string]$Owner,
  [string]$Classification = "internal",
  [string]$Name = "card-dispute-router",
  [string]$Model = "gpt-5-mini",
  [string]$OutDir = (Join-Path $env:TEMP "card-dispute-demo"),
  [switch]$Fake
)
$ErrorActionPreference = "Stop"
$skill  = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $HOME ".venvs\foundry-demo\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Missing $python. Create it first (see the README steps)." }
$env:PYTHONPATH = ""                       # never rely on loose modules
$env:PYTHONIOENCODING = "utf-8"
$env:OTEL_SDK_DISABLED = "true"

# endpoint: environment first, then a local .env beside this script
$envFile = Join-Path $PSScriptRoot ".env"
if (-not $env:FOUNDRY_PROJECT_ENDPOINT -and (Test-Path $envFile)) {
  Get-Content $envFile | ForEach-Object { if ($_ -match '^\s*FOUNDRY_PROJECT_ENDPOINT\s*=\s*(.+?)\s*$') { $env:FOUNDRY_PROJECT_ENDPOINT = $Matches[1] } }
}
if (-not $Fake -and -not $env:FOUNDRY_PROJECT_ENDPOINT) { throw "FOUNDRY_PROJECT_ENDPOINT is not set (environment or $envFile). Use -Fake to rehearse offline." }

$work = Join-Path $env:TEMP "card-dispute-work"
New-Item -ItemType Directory -Force $work | Out-Null
if (Test-Path $OutDir) { Remove-Item -Recurse -Force $OutDir }

function Step($t) { Write-Host "`n=== $t" -ForegroundColor Cyan }

Step "1. Questions the skill asks (preset option first, marked Recommended)"
& $python (Join-Path $skill "overlay\intake.py") questions --preset (Join-Path $skill "examples\card-dispute\preset.json") |
  & $python -c "import json,sys
for b in json.load(sys.stdin):
    for q in b:
        print('  ['+q['id']+'] '+q['question'])
        for o in q.get('options',[]): print('       - '+o['label'])
        if q['kind']=='text': print('       (free text)')"

Step "2. Build the spec from your answers (everything else comes from the preset)"
$answers = @{ owner = $Owner; classification = $Classification; name = $Name; model = $Model } | ConvertTo-Json
Set-Content -Path (Join-Path $work "answers.json") -Value $answers -Encoding utf8
& $python (Join-Path $skill "overlay\intake.py") build --preset (Join-Path $skill "examples\card-dispute\preset.json") `
  --answers (Join-Path $work "answers.json") --out (Join-Path $work "spec.json")
if ($LASTEXITCODE -ne 0) { throw "build failed" }

Step "3. Create WITHOUT confirming (must refuse and write nothing)"
& $python (Join-Path $skill "overlay\intake.py") create --spec (Join-Path $work "spec.json") --out $OutDir
Write-Host ("   exit code {0}; folder created: {1}" -f $LASTEXITCODE, (Test-Path $OutDir))

Step "4. Create after you confirm (hosted + router-specialists)"
& $python (Join-Path $skill "overlay\intake.py") create --spec (Join-Path $work "spec.json") --out $OutDir `
  --confirm-kind hosted --confirm-pattern router-specialists `
  --recommend-pattern router-specialists --recommend-why "dispute intake and fraud reports are independent topics"
if ($LASTEXITCODE -ne 0) { throw "create failed" }

Step "5. Session check on the generated agent (reads source only)"
Push-Location $OutDir
try { & $python preflight.py main.py } finally { Pop-Location }

Step "6. Run the generated router ($(if ($Fake) { 'FAKE model' } else { "REAL model: $Model" }))"
$demoArgs = @((Join-Path $skill "examples\card-dispute\demo.py"), $OutDir)
if (-not $Fake) { $demoArgs += @("--live", "--model", $Model) }
& $python @demoArgs

Step "7. What the contract says is NOT verified"
Select-String -Path (Join-Path $OutDir "docs\runtime-contract.md") -Pattern "Claims implementation|Observed|Blocks readiness" | ForEach-Object { "  " + $_.Line.Trim() }
Write-Host "`nDone. Nothing was deployed, installed or dispatched. Generated agent: $OutDir" -ForegroundColor Green
