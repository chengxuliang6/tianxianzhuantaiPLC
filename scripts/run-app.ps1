[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$AppArgs
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "未找到项目 Python 环境。请先在项目根目录运行 .\scripts\setup.ps1。"
}

Push-Location $ProjectRoot
try {
    & $Python -m turntable_control.main @AppArgs
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
