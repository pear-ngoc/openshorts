# dev-up.ps1 — Start OpenShorts in dev mode on Windows (PowerShell)
#
# Usage:
#   .\scripts\dev-up.ps1
#
# Prerequisites:
#   - Docker Desktop with WSL2 backend installed
#   - Docker Compose v2 plugin (docker compose, not docker-compose)
#

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
Set-Location $ProjectDir

Write-Host "📁 Project root: $ProjectDir" -ForegroundColor Cyan
Write-Host ""

# Ensure runtime directories exist on the host
foreach ($dir in @("uploads", "output", "outputs", "temp", "clips")) {
    $path = Join-Path $ProjectDir $dir
    if (-not (Test-Path $path)) {
        New-Item -ItemType Directory -Path $path | Out-Null
        Write-Host "   Created $dir/" -ForegroundColor Green
    } else {
        Write-Host "   $dir/ — exists" -ForegroundColor Gray
    }
}

Write-Host ""
Write-Host "🚀 Starting OpenShorts in DEV mode..." -ForegroundColor Cyan
Write-Host "   - Backend: http://localhost:8000"
Write-Host "   - Frontend: http://localhost:5175"
Write-Host ""
Write-Host "   Press Ctrl+C to stop, or run: docker compose -f docker-compose.yml -f docker-compose.dev.yml down" -ForegroundColor Yellow
Write-Host ""

& docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
