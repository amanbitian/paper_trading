# Start the paper trading app.
# Usage:
#   .\run.ps1              # foreground
#   .\run.ps1 -d           # background
#   .\run.ps1 check        # verify Docker + setup
#   .\run.ps1 stop

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Get-PythonCommand {
    if (Get-Command py -ErrorAction SilentlyContinue) { return @("py", "-3") }
    if (Get-Command python -ErrorAction SilentlyContinue) { return @("python") }
    return $null
}

$python = Get-PythonCommand
if ($python) {
    & @python scripts/run.py @args
    exit $LASTEXITCODE
}

Write-Host "Error: Python not found. Install Python 3.11+ or use 'py' from the Microsoft Store." -ForegroundColor Red
Write-Host "Then run: py -3 scripts/run.py check"
exit 1
