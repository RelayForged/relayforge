# PayFlow from this machine, against the deployed Relay (deploy/aws).
# Same commands as `docker compose exec payflow python -m examples.payflow`:
#
#   deploy\aws\payflow.ps1 onboard acme http://<stores-ip>:8071/webhook
#   deploy\aws\payflow.ps1 pay acme 499 --count 5 --interval 1
#
# The merchant list is kept in deploy\aws\.state\merchants.json.

$stateFile = Join-Path $PSScriptRoot ".state\state.env"
if (-not (Test-Path $stateFile)) { throw "Nothing deployed yet; run deploy/aws/provision.sh and deploy.sh" }
$state = Get-Content $stateFile -Raw | ConvertFrom-StringData

$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$python = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

$env:RELAY_URL = "http://$($state.RELAY_IP):8000"
$env:PAYFLOW_STATE = Join-Path $PSScriptRoot ".state\merchants.json"
Push-Location $repo
try {
    & $python -m examples.payflow @args
}
finally {
    Pop-Location
    Remove-Item Env:RELAY_URL, Env:PAYFLOW_STATE
}
