# One-command local demo (Windows PowerShell).
#   .\start.ps1          -> builds the UI if needed, starts the twin on http://127.0.0.1:8000
#   .\start.ps1 -Dev     -> backend on :8000 plus Vite dev server with hot reload on :5173
param([switch]$Dev)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py = Join-Path $root "backend\.venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "Creating Python 3.12 virtual environment..."
    py -3.12 -m venv (Join-Path $root "backend\.venv")
    & $py -m pip install --upgrade pip
    & $py -m pip install -r (Join-Path $root "backend\requirements-dev.txt")
    & $py -m pip install torch==2.14.0 --index-url https://download.pytorch.org/whl/cpu
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { $env:Path += ";C:\Program Files\nodejs" }

Push-Location (Join-Path $root "frontend")
if (-not (Test-Path "node_modules")) { npm install }
if (-not $Dev) { npm run build }
Pop-Location

Push-Location (Join-Path $root "backend")
& $py -m app.train   # trains only models that are missing (first run: a few minutes)
if ($Dev) {
    Start-Process -FilePath $py -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"
    Pop-Location
    Push-Location (Join-Path $root "frontend")
    npm run dev
    Pop-Location
} else {
    Write-Host "Open http://127.0.0.1:8000"
    & $py -m uvicorn app.main:app --host 127.0.0.1 --port 8000
    Pop-Location
}
