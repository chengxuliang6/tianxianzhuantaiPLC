[CmdletBinding()]
param(
    [string]$DataDir = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "未找到项目 Python 环境。请先在项目根目录运行 .\scripts\setup.ps1。"
}
if ([string]::IsNullOrWhiteSpace($DataDir)) {
    $DataDir = Join-Path $ProjectRoot "data\simulator"
}
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

Push-Location $ProjectRoot
try {
    & $Python -m turntable_control.main --simulator --data-dir $DataDir
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
