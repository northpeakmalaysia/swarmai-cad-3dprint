# install.ps1 — one-time setup for the SwarmAI cad-3dprint plugin.
# Installs the Node dependency (zod) and the Python geometry engine deps.
param(
  [string]$Python = "python"
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "==> Installing Node dependency (zod) ..." -ForegroundColor Cyan
Push-Location $here
try {
  if (Get-Command npm -ErrorAction SilentlyContinue) {
    npm install --no-audit --no-fund
  } else {
    Write-Warning "npm not found — the plugin needs 'zod' resolvable at runtime."
  }
} finally { Pop-Location }

Write-Host "==> Installing Python engine deps ..." -ForegroundColor Cyan
& $Python -m pip install -r (Join-Path $here "requirements.txt")

Write-Host "==> Selftest ..." -ForegroundColor Cyan
'{"action":"selftest"}' | & $Python (Join-Path $here "python\cad_runner.py")

Write-Host "`nDone. Register the plugin by adding this to ~/.swarmai/plugins.yaml (or %APPDATA%\swarmai\plugins.yaml):" -ForegroundColor Green
Write-Host @"
version: 1
plugins:
  - module: "$($here -replace '\\','\\')\\dist\\index.js"
    enabled: true
    config:
      pythonPath: $Python
      outputDir: "$here\\output"
"@ -ForegroundColor Gray
