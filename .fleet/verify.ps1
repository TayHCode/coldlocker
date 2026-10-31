$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
python -B tools/verify_export.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
