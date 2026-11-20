# ASCII-only: Windows PowerShell 5.1 may misread UTF-8 without a BOM.
[CmdletBinding()]
param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [switch]$NoPause,
    [switch]$SkipInstall,
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Continue'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$rootPath = [IO.Path]::GetFullPath($Root)
$botDir = Join-Path $rootPath 'nonebot'
$venvDir = Join-Path $botDir '.venv'
$python = Join-Path $venvDir 'Scripts\python.exe'
$requirements = Join-Path $rootPath 'requirements.lock'
$snowNode = Join-Path $rootPath 'snowluma\node.exe'
$snowEntry = Join-Path $rootPath 'snowluma\index.mjs'
$ragIndex = Join-Path $rootPath 'rag\index\vectors.npy'
$ragPython = Join-Path $rootPath 'rag\.venv\Scripts\python.exe'
if (Test-Path -LiteralPath (Join-Path $rootPath 'rag\.venv-gpu\Scripts\python.exe')) {
    $ragPython = Join-Path $rootPath 'rag\.venv-gpu\Scripts\python.exe'
}
$gsuidPython = Join-Path $rootPath 'gsuid\core\.venv\Scripts\python.exe'
$pluginSettings = Join-Path $botDir 'data\plugins.json'

function Write-Section($Message) {
    Write-Host "[deps] $Message" -ForegroundColor Cyan
}

function Write-Ok($Message) {
    Write-Host "  [OK] $Message" -ForegroundColor Green
}

function Write-Warn($Message) {
    Write-Host "  [--] $Message" -ForegroundColor Yellow
}

function Stop-Check($Message) {
    Write-Host "  [!!] $Message" -ForegroundColor Red
    if (-not $NoPause -and -not $env:QQBOT_NO_PAUSE -and [Environment]::UserInteractive) {
        Write-Host ''
        [void](Read-Host 'Press Enter to close')
    }
    exit 1
}

function Invoke-PipInstall {
    Write-Section 'Installing/repairing requirements.lock; no service starts until this completes ...'
    & $python -m pip install --disable-pip-version-check --no-input -r $requirements
    if ($LASTEXITCODE -ne 0) {
        Stop-Check "pip install failed (exit $LASTEXITCODE); no service was started."
    }
    Set-Content -LiteralPath $marker -Value $requirementsHash -NoNewline -Encoding ascii
}

trap {
    Write-Host "  [!!] Dependency check failed: $($_.Exception.Message)" -ForegroundColor Red
    if (-not $NoPause -and -not $env:QQBOT_NO_PAUSE -and [Environment]::UserInteractive) {
        Write-Host ''
        [void](Read-Host 'Press Enter to close')
    }
    exit 1
}

Write-Host '==========================================' -ForegroundColor Cyan
Write-Host '  Startup dependency preflight' -ForegroundColor Cyan
Write-Host '==========================================' -ForegroundColor Cyan

if (-not (Test-Path -LiteralPath (Join-Path $botDir 'bot.py') -PathType Leaf)) {
    Stop-Check "Not a valid checkout: missing $botDir\bot.py"
}
if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) {
    Stop-Check "Missing dependency lock: $requirements"
}

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    if ($CheckOnly) {
        Stop-Check "NoneBot virtual environment is missing: $python"
    }
    Write-Section 'NoneBot venv is missing; trying to create it with Python 3.12 ...'
    $created = $false
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        & $py.Source -3.12 -m venv $venvDir
        if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $python -PathType Leaf)) {
            $created = $true
        }
    }
    if (-not $created) {
        $pythonCmd = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($pythonCmd) {
            $versionOutput = & $pythonCmd.Source -c "import sys; print(str(sys.version_info[0]) + '.' + str(sys.version_info[1]))" 2>&1
            $versionText = ($versionOutput -join "`n").Trim()
            if ($LASTEXITCODE -eq 0 -and $versionText -eq '3.12') {
                & $pythonCmd.Source -m venv $venvDir
                if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $python -PathType Leaf)) {
                    $created = $true
                }
            }
        }
    }
    if (-not $created) {
        Stop-Check 'Could not create the NoneBot venv. Install Python 3.12, then run: py -3.12 -m venv nonebot\.venv'
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $venvDir 'pyvenv.cfg') -PathType Leaf)) {
    Stop-Check "NoneBot venv is incomplete (missing pyvenv.cfg): $venvDir"
}
$venvVersion = (& $python -c "import sys; print(str(sys.version_info[0]) + '.' + str(sys.version_info[1]))" 2>&1) -join "`n"
$venvVersion = $venvVersion.Trim()
if ($LASTEXITCODE -ne 0) {
    Stop-Check "NoneBot Python cannot run (exit $LASTEXITCODE): $python`n$venvVersion"
}
if ($venvVersion -ne '3.12') {
    Stop-Check "NoneBot venv uses Python $venvVersion (requires 3.12): $venvDir`nRecreate it from the repo root: Remove-Item -Recurse -Force nonebot\.venv; py -3.12 -m venv nonebot\.venv"
}
Write-Ok "NoneBot Python: $python"

if (-not (Test-Path -LiteralPath $snowNode -PathType Leaf)) {
    Stop-Check "SnowLuma runtime is missing: $snowNode"
}
if (-not (Test-Path -LiteralPath $snowEntry -PathType Leaf)) {
    Stop-Check "SnowLuma entry point is missing: $snowEntry"
}
Write-Ok "SnowLuma runtime: $snowNode"

if ($CheckOnly) {
    Write-Ok 'File checks passed (CheckOnly; pip/import were not run)'
    exit 0
}

& $python -m pip --version *> $null
if ($LASTEXITCODE -ne 0) {
    if ($SkipInstall) {
        Stop-Check 'pip is unavailable, and -SkipInstall was requested; no service was started.'
    }
    Write-Section 'pip is unavailable; trying ensurepip ...'
    & $python -m ensurepip --upgrade
    if ($LASTEXITCODE -ne 0) {
        Stop-Check "pip is unavailable (exit $LASTEXITCODE); no service was started."
    }
}
Write-Ok 'pip is available'

$requirementsHash = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash.ToLowerInvariant()
$marker = Join-Path $venvDir '.qqbot-requirements.sha256'
$markerHash = ''
if (Test-Path -LiteralPath $marker -PathType Leaf) {
    $markerText = Get-Content -LiteralPath $marker -Raw
    if ($null -ne $markerText) {
        $markerHash = $markerText.Trim().ToLowerInvariant()
    }
}
$needsInstall = $markerHash -ne $requirementsHash

if ($needsInstall) {
    if ($SkipInstall) {
        Write-Warn 'requirements.lock changed, but -SkipInstall was requested; checking the existing environment'
    } else {
        Invoke-PipInstall
    }
}

$pipCheck = & $python -m pip check 2>&1
if ($LASTEXITCODE -ne 0) {
    if ($SkipInstall) {
        Stop-Check "pip check failed (-SkipInstall prevents repair):`n$pipCheck"
    }
    Write-Warn 'pip check failed; repairing from requirements.lock ...'
    Invoke-PipInstall
    $pipCheck = & $python -m pip check 2>&1
    if ($LASTEXITCODE -ne 0) {
        Stop-Check "Dependency conflicts remain:`n$pipCheck"
    }
}
Write-Ok 'pip check passed'

function Invoke-ImportCheck {
    Push-Location -LiteralPath $botDir
    try {
        & $python -c "import nonebot, importlib.util; assert importlib.util.find_spec('muicebot') is not None" 2>&1
    } finally {
        Pop-Location
    }
}

$importCheck = Invoke-ImportCheck
if ($LASTEXITCODE -ne 0) {
    if ($SkipInstall) {
        Stop-Check "NoneBot/MuiceBot import check failed (-SkipInstall prevents repair):`n$importCheck"
    }
    Write-Warn 'NoneBot/MuiceBot import check failed; repairing from requirements.lock ...'
    Invoke-PipInstall
    $importCheck = Invoke-ImportCheck
    if ($LASTEXITCODE -ne 0) {
        Stop-Check "NoneBot/MuiceBot still cannot be imported:`n$importCheck"
    }
}
Write-Ok 'nonebot imports; muicebot is installed'

if (Test-Path -LiteralPath $ragIndex -PathType Leaf) {
    if (Test-Path -LiteralPath $ragPython -PathType Leaf) {
        Write-Ok 'Optional RAG service: index and venv are ready'
    } else {
        Write-Warn "RAG index exists but its venv is missing: $ragPython (RAG will be skipped)"
    }
} else {
    Write-Warn 'Optional RAG service is not configured (rag\index\vectors.npy is missing; RAG will be skipped)'
}

$gsuidEnabled = $true
if (Test-Path -LiteralPath $pluginSettings -PathType Leaf) {
    try {
        $pluginData = Get-Content -LiteralPath $pluginSettings -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($pluginData.disabled -contains 'gsuid') {
            $gsuidEnabled = $false
        }
    } catch {
        Write-Warn "Could not read plugin switches: $pluginSettings (assuming gsuid is enabled)"
    }
}
if (-not $gsuidEnabled) {
    Write-Warn 'Optional gsuid_core is disabled by the plugin manager'
} elseif (Test-Path -LiteralPath $gsuidPython -PathType Leaf) {
    Write-Ok 'Optional gsuid_core: venv is ready'
} else {
    Write-Warn "Optional gsuid_core is not installed: $gsuidPython (gsuid will be skipped)"
}

foreach ($configFile in @((Join-Path $botDir '.env'), (Join-Path $botDir 'configs\models.yml'))) {
    if (-not (Test-Path -LiteralPath $configFile -PathType Leaf)) {
        Write-Warn "Missing configuration: $configFile (the bot may fail to start)"
    }
}

Write-Host ''
Write-Host 'Dependency preflight passed; starting services.' -ForegroundColor Green
exit 0
