$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $projectRoot

$env:PYTHONPATH = $projectRoot

$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
  throw "Python venv not found: $pythonExe"
}

# Align local backend port with docker-host-exposed backend port.
& $pythonExe -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8002
