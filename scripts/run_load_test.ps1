$ErrorActionPreference = "Stop"

$workspace = Split-Path -Parent $PSScriptRoot
$python = Join-Path $workspace "venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Project virtual environment not found: $python"
}

& $python (Join-Path $workspace "scripts\load_test_backends.py") @args
exit $LASTEXITCODE
