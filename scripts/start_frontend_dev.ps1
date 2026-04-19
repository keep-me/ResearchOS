$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$frontendRoot = Join-Path $projectRoot "frontend"
Set-Location $frontendRoot

# Align local frontend/backend ports with docker host ports.
$env:VITE_PROXY_TARGET = "http://127.0.0.1:8002"
npx vite --host 127.0.0.1 --port 3002 --strictPort
