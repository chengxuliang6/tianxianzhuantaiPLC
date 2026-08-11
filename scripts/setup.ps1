$ErrorActionPreference = "Stop"

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $repositoryRoot ".venv"
$versionCheck = "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)"
$pythonExe = $null
$pythonArgs = @()

$pathPython = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if ($pathPython) {
    & $pathPython.Source -c $versionCheck
    if ($LASTEXITCODE -eq 0) {
        $pythonExe = $pathPython.Source
    }
}

if (-not $pythonExe) {
    $pyLauncher = Get-Command py -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pyLauncher) {
        $previousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $null = & $pyLauncher.Source -3.12 -c $versionCheck 2>$null
        $pyExitCode = $LASTEXITCODE
        $ErrorActionPreference = $previousErrorActionPreference
        if ($pyExitCode -eq 0) {
            $pythonExe = $pyLauncher.Source
            $pythonArgs = @("-3.12")
        }
    }
}

if (-not $pythonExe -and $env:USERPROFILE) {
    $bundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if (Test-Path -LiteralPath $bundledPython) {
        & $bundledPython -c $versionCheck
        if ($LASTEXITCODE -eq 0) {
            $pythonExe = $bundledPython
        }
    }
}

if (-not $pythonExe) {
    throw ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("5pyq5om+5Yiw5Y+v55So55qEIFB5dGhvbiAzLjEy44CC6K+35a6J6KOFIFB5dGhvbiAzLjEy77yM5oiW56Gu6K6kIENvZGV4IGJ1bmRsZWQgUHl0aG9uIOi3r+W+hOWPr+eUqOWQjumHjeivleOAgg==")))
}

& $pythonExe @pythonArgs -m venv $venvPath
if ($LASTEXITCODE -ne 0) {
    throw "Failed to create the virtual environment."
}

$venvPython = Join-Path $venvPath "Scripts\python.exe"
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "Failed to upgrade pip."
}

& $venvPython -m pip install -e "$repositoryRoot\pc[dev]"
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install the project and development dependencies."
}
