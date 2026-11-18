#Requires -Version 5.1
<#
.SYNOPSIS
    Install or refresh the pinned gsuid_core service and its plugin stack.

.DESCRIPTION
    Layout produced (all generated at runtime, nothing vendored in this repo):
      gsuid\core                                       gsuid_core clone + .venv + data
      gsuid\core\gsuid_core\plugins\XutheringWavesUID
      gsuid\core\gsuid_core\plugins\EndUID
      gsuid\core\gsuid_core\plugins\RoverSign

    Re-running is safe: existing clones are reused and re-pinned to the exact
    commit recorded in versions.env.
#>
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ScriptDir    = $PSScriptRoot
$RepoRoot     = Split-Path -Parent $ScriptDir
$VersionsFile = Join-Path $ScriptDir 'versions.env'
$CoreDir      = Join-Path $ScriptDir 'core'
$PluginsDir   = Join-Path $CoreDir 'gsuid_core\plugins'

function Write-Step {
    param([string]$Message)
    Write-Host "==> $Message"
}

function Stop-Install {
    param([string]$Message)
    Write-Host "error: $Message" -ForegroundColor Red
    exit 1
}

# Runs a native executable, captures stdout and the exit code, and never turns
# stderr into a terminating error (with $ErrorActionPreference = 'Stop', a
# plain `2>$null` redirect of native stderr would throw in Windows PowerShell 5.1).
function Invoke-Native {
    param(
        [string]$Exe,
        [string[]]$ArgList = @(),
        [switch]$Quiet
    )

    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        if ($Quiet) {
            $stdout = & $Exe @ArgList 2>$null
        }
        else {
            $stdout = & $Exe @ArgList
        }
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }

    [pscustomobject]@{
        ExitCode = $exitCode
        Text     = (($stdout | ForEach-Object { "$_" }) -join "`n")
    }
}

function Read-VersionsEnv {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        Stop-Install "missing $Path (expected next to this script)"
    }

    $values = @{}
    $lineNo = 0
    foreach ($line in Get-Content -LiteralPath $Path) {
        $lineNo++
        $trimmed = $line.Trim()
        if ($trimmed.Length -eq 0 -or $trimmed.StartsWith('#')) { continue }

        $splitAt = $trimmed.IndexOf('=')
        if ($splitAt -lt 1) {
            Stop-Install "versions.env line ${lineNo}: expected KEY=VALUE"
        }

        $key = $trimmed.Substring(0, $splitAt).Trim()
        $value = $trimmed.Substring($splitAt + 1).Trim()
        if ($value.Length -eq 0) {
            Stop-Install "versions.env line ${lineNo}: empty value for $key"
        }
        $values[$key] = $value
    }

    return $values
}

function Assert-Tool {
    param([string]$Name, [string]$Hint)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Stop-Install "'$Name' is required but was not found on PATH`n      $Hint"
    }
}

# Install-PinnedClone URL DESTINATION SHA NAME
# Reuse DESTINATION when it already is a clone, otherwise shallow-clone URL
# into it, then force HEAD to exactly SHA (fetching that commit when the
# shallow clone does not carry it because the branch head moved after the pin
# was made).
function Install-PinnedClone {
    param(
        [string]$Url,
        [string]$Destination,
        [string]$Sha,
        [string]$Name
    )

    $gitDir = Join-Path $Destination '.git'

    if (Test-Path -LiteralPath $gitDir) {
        Write-Step "$Name : reusing existing clone at $Destination"
    }
    else {
        if (Test-Path -LiteralPath $Destination) {
            $existing = @(Get-ChildItem -LiteralPath $Destination -Force -ErrorAction SilentlyContinue)
            if ($existing.Count -gt 0) {
                Stop-Install "$Name : $Destination exists but is not a git clone; move it away and re-run"
            }
            Remove-Item -LiteralPath $Destination -Force
        }

        $parent = Split-Path -Parent $Destination
        if (-not (Test-Path -LiteralPath $parent)) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }

        Write-Step "$Name : cloning $Url"
        $clone = Invoke-Native -Exe 'git' -ArgList @('clone', '--quiet', '--depth', '1', '--', $Url, $Destination)
        if ($clone.ExitCode -ne 0) {
            Stop-Install "$Name : git clone failed ($Url)"
        }
    }

    $head = Invoke-Native -Exe 'git' -ArgList @('-C', $Destination, 'rev-parse', 'HEAD') -Quiet
    if ($head.ExitCode -ne 0 -or $head.Text -ne $Sha) {
        $pinned = Invoke-Native -Exe 'git' -ArgList @('-C', $Destination, 'rev-parse', '--verify', '--quiet', "$Sha^{commit}") -Quiet

        if ($pinned.ExitCode -ne 0) {
            Write-Step "$Name : fetching pinned commit $Sha"
            $null = Invoke-Native -Exe 'git' -ArgList @('-C', $Destination, 'fetch', '--quiet', '--depth', '1', 'origin', $Sha) -Quiet
            $pinned = Invoke-Native -Exe 'git' -ArgList @('-C', $Destination, 'rev-parse', '--verify', '--quiet', "$Sha^{commit}") -Quiet

            if ($pinned.ExitCode -ne 0) {
                Write-Step "$Name : shallow fetch did not yield $Sha, fetching full history"
                $fetch = Invoke-Native -Exe 'git' -ArgList @('-C', $Destination, 'fetch', '--quiet', '--unshallow', 'origin') -Quiet
                if ($fetch.ExitCode -ne 0) {
                    $fetch = Invoke-Native -Exe 'git' -ArgList @('-C', $Destination, 'fetch', '--quiet', 'origin') -Quiet
                }
                if ($fetch.ExitCode -ne 0) {
                    Stop-Install "$Name : could not fetch from $Url"
                }

                $pinned = Invoke-Native -Exe 'git' -ArgList @('-C', $Destination, 'rev-parse', '--verify', '--quiet', "$Sha^{commit}") -Quiet
                if ($pinned.ExitCode -ne 0) {
                    Stop-Install "$Name : commit $Sha does not exist upstream ($Url)"
                }
            }
        }

        Write-Step "$Name : checking out $Sha"
        $checkout = Invoke-Native -Exe 'git' -ArgList @('-C', $Destination, '-c', 'advice.detachedHead=false', 'checkout', '--quiet', '--force', '--detach', $Sha)
        if ($checkout.ExitCode -ne 0) {
            Stop-Install "$Name : could not check out $Sha"
        }
    }

    $final = Invoke-Native -Exe 'git' -ArgList @('-C', $Destination, 'rev-parse', 'HEAD') -Quiet
    if ($final.ExitCode -ne 0) {
        Stop-Install "$Name : cannot read HEAD in $Destination"
    }
    if ($final.Text -ne $Sha) {
        Stop-Install "$Name : expected commit $Sha but HEAD is $($final.Text)"
    }

    Write-Step "$Name : pinned at $Sha"
}

$Versions = Read-VersionsEnv -Path $VersionsFile

foreach ($key in @(
        'GSUID_CORE_REPO', 'GSUID_CORE_SHA',
        'XUTHERINGWAVESUID_REPO', 'XUTHERINGWAVESUID_SHA',
        'ENDUID_REPO', 'ENDUID_SHA',
        'ROVERSIGN_REPO', 'ROVERSIGN_SHA',
        'GENSHINUID_ADAPTER_REQ'
    )) {
    if (-not $Versions.ContainsKey($key)) {
        Stop-Install "versions.env does not define $key"
    }
}

Assert-Tool -Name 'git' -Hint 'git: https://git-scm.com/downloads'
Assert-Tool -Name 'uv' -Hint 'uv:  https://docs.astral.sh/uv/getting-started/installation/'

Write-Step "repository root: $RepoRoot"

Install-PinnedClone -Url $Versions['GSUID_CORE_REPO'] -Destination $CoreDir `
    -Sha $Versions['GSUID_CORE_SHA'] -Name 'gsuid_core'
Install-PinnedClone -Url $Versions['XUTHERINGWAVESUID_REPO'] -Destination (Join-Path $PluginsDir 'XutheringWavesUID') `
    -Sha $Versions['XUTHERINGWAVESUID_SHA'] -Name 'XutheringWavesUID'
Install-PinnedClone -Url $Versions['ENDUID_REPO'] -Destination (Join-Path $PluginsDir 'EndUID') `
    -Sha $Versions['ENDUID_SHA'] -Name 'EndUID'
Install-PinnedClone -Url $Versions['ROVERSIGN_REPO'] -Destination (Join-Path $PluginsDir 'RoverSign') `
    -Sha $Versions['ROVERSIGN_SHA'] -Name 'RoverSign'

Write-Step 'installing Python 3.13 via uv (no-op when already present)'
$pythonInstall = Invoke-Native -Exe 'uv' -ArgList @('python', 'install', '3.13')
if ($pythonInstall.ExitCode -ne 0) {
    Stop-Install 'uv python install 3.13 failed'
}

Write-Step "syncing gsuid_core environment in $CoreDir"
Push-Location -LiteralPath $CoreDir
try {
    $sync = Invoke-Native -Exe 'uv' -ArgList @('sync', '--python', '3.13')
    if ($sync.ExitCode -ne 0) {
        Stop-Install 'uv sync failed in gsuid\core'
    }

    Write-Step 'applying EndUID render-size compatibility patch'
    $renderPatch = Invoke-Native -Exe 'uv' -ArgList @(
        'run', 'python', (Join-Path $ScriptDir 'patches\enduid-render-size.py'), $CoreDir
    )
    if ($renderPatch.ExitCode -ne 0) {
        Stop-Install 'EndUID render-size compatibility patch failed'
    }

    Write-Step 'applying command-prefix display patch'
    $prefixPatch = Invoke-Native -Exe 'uv' -ArgList @(
        'run', 'python', (Join-Path $ScriptDir 'patches\command-prefix-display.py'), $CoreDir
    )
    if ($prefixPatch.ExitCode -ne 0) {
        Stop-Install 'command-prefix display patch failed'
    }

    Write-Step 'ensuring pip is importable inside the core venv (required for plugin auto-install)'
    $ensurePip = Invoke-Native -Exe 'uv' -ArgList @('run', 'python', '-m', 'ensurepip')
    if ($ensurePip.ExitCode -ne 0) {
        Stop-Install 'python -m ensurepip failed in gsuid\core'
    }

    Write-Step 'installing plugin runtime dependencies into the core venv'
    $deps = Invoke-Native -Exe 'uv' -ArgList @(
        'pip', 'install', '--python', '.venv\Scripts\python.exe',
        'pypinyin', 'rapidfuzz', 'opencv-python', 'fonttools'
    )
    if ($deps.ExitCode -ne 0) {
        Stop-Install 'uv pip install of plugin runtime dependencies failed'
    }
}
finally {
    Pop-Location
}

Write-Host ''
Write-Host 'Installed pinned versions:'
Write-Host "  gsuid_core        $($Versions['GSUID_CORE_SHA'])"
Write-Host "  XutheringWavesUID $($Versions['XUTHERINGWAVESUID_SHA'])"
Write-Host "  EndUID            $($Versions['ENDUID_SHA'])"
Write-Host "  RoverSign         $($Versions['ROVERSIGN_SHA'])"
Write-Host "  NoneBot adapter   $($Versions['GENSHINUID_ADAPTER_REQ'])"
Write-Host ''
Write-Host 'Next steps:'
Write-Host '  1. Start the core:          cd gsuid/core && uv run core'
Write-Host '  2. Open http://127.0.0.1:8765/app and set WS_TOKEN plus your master accounts.'
Write-Host "  3. Install the adapter into the NoneBot venv ($($Versions['GENSHINUID_ADAPTER_REQ'])),"
Write-Host '     put the same WS_TOKEN into the NoneBot config, then start NoneBot'
Write-Host '     from the repository root.'
