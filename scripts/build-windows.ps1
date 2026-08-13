[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Entry = Join-Path $ProjectRoot "pc\src\turntable_control_entry.py"
$SourcePath = Join-Path $ProjectRoot "pc\src"
$Exe = Join-Path $ProjectRoot "dist\TurntableControl\TurntableControl.exe"
$SmokeData = Join-Path $ProjectRoot "data\package-smoke"
$OriginalPath = $env:PATH

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "未找到项目 Python 环境。请先运行 .\scripts\setup.ps1。"
}

Push-Location $ProjectRoot
try {
    $BasePython = & $Python -c "import sys; print(sys.base_prefix)"
    if ($LASTEXITCODE -ne 0) { throw "无法确定 Python 运行目录。" }
    $PythonDlls = Join-Path $BasePython.Trim() "DLLs"
    if (-not (Test-Path -LiteralPath $PythonDlls -PathType Container)) {
        throw "未找到 Python DLL 目录。"
    }
    # Keep Python's matching OpenSSL DLLs ahead of unrelated Anaconda/Git DLLs.
    $env:PATH = "$PythonDlls;$OriginalPath"
    & $Python -m PyInstaller --noconfirm --clean --windowed --name TurntableControl --paths $SourcePath $Entry
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败。" }
    if (-not (Test-Path -LiteralPath $Exe -PathType Leaf)) {
        throw "构建结束但未找到 TurntableControl.exe。"
    }
    New-Item -ItemType Directory -Force -Path $SmokeData | Out-Null
    $Smoke = Start-Process -FilePath $Exe -ArgumentList @("--simulator", "--package-smoke", "--data-dir", $SmokeData) -PassThru -Wait -WindowStyle Hidden
    if ($Smoke.ExitCode -ne 0) { throw "模拟器安全启动检查失败，退出码 $($Smoke.ExitCode)。" }
    Write-Host "构建与无网络启动检查通过：$Exe"
}
finally {
    $env:PATH = $OriginalPath
    Pop-Location
}
