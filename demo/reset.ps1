# Clean demo start: wipe all stack data, rebuild, wait for health, smoke check.
# Brings up Demo 2 (demo/SDK_DEMO.md) too, with empty merchant inboxes.
#
#   powershell -ExecutionPolicy Bypass -File demo\reset.ps1
#
# DESTRUCTIVE: `down -v` deletes the Postgres, Kafka and Redis volumes.
# Every event, delivery and attempt recorded so far is gone.

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "Stopping the stack and deleting its volumes..."
docker compose --profile sdk-demo down -v
if ($LASTEXITCODE -ne 0) { throw "docker compose down failed" }

Write-Host "Building and starting..."
docker compose --profile sdk-demo up -d --build
if ($LASTEXITCODE -ne 0) { throw "docker compose up failed" }

$services = @("kafka", "postgres", "redis", "api", "dashboard", "simulator", "store-acme", "store-bluebird")
$deadline = (Get-Date).AddMinutes(4)
while ($true) {
    $status = docker compose --profile sdk-demo ps --format "{{.Service}} {{.Status}}"
    $healthy = @($services | Where-Object { $s = $_; $status | Where-Object { $_ -like "$s *(healthy)*" } })
    if ($healthy.Count -eq $services.Count) { break }
    if ((Get-Date) -gt $deadline) {
        $status
        throw "Services not healthy after 4 minutes"
    }
    Start-Sleep -Seconds 3
}
Write-Host "All services healthy."

$python = Join-Path (Get-Location) ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
& $python demo\smoke_check.py
if ($LASTEXITCODE -ne 0) { throw "Smoke check failed" }

Write-Host ""
Write-Host "Ready: http://localhost:8090  (Demo 2 inboxes: http://localhost:8071, http://localhost:8072)"
