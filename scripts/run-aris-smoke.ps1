param(
    [switch]$SmokeOnly,
    [switch]$PytestOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

function Resolve-Python {
    $venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        return $venvPython
    }
    return "python"
}

$python = Resolve-Python

if (-not $SmokeOnly) {
    Write-Host "[ARIS] Running targeted pytest suite..."
    & $python -m pytest `
        tests/test_project_multi_agent_runner.py `
        tests/test_project_report_formatter.py `
        tests/test_project_workflow_runner.py `
        tests/test_aris_feature_matrix.py `
        -q
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

if (-not $PytestOnly) {
    Write-Host "[ARIS] Running end-to-end smoke script..."
    & $python scripts/aris_workflow_smoke.py
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

Write-Host "[ARIS] Smoke checks completed."
