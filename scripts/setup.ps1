$ErrorActionPreference = "Stop"

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $repositoryRoot ".venv"

python -m venv $venvPath
& (Join-Path $venvPath "Scripts\\python.exe") -m pip install --upgrade pip
& (Join-Path $venvPath "Scripts\\python.exe") -m pip install -e "$repositoryRoot\\pc[dev]"
