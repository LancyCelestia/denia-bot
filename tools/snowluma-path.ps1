# ASCII-only: Windows PowerShell 5.1 may misread UTF-8 without a BOM.
[CmdletBinding()]
param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [string]$SnowLumaPath = ''
)

$ErrorActionPreference = 'Continue'
$rootPath = [IO.Path]::GetFullPath($Root)
$version = '2.4.2'
$zipPattern = '(?i)^SnowLuma[-_ ]*v?' + [regex]::Escape($version) + '[-_ ]*win[-_ ]*x64.*\.zip$'
$pathFile = Join-Path $rootPath 'logs\snowluma-home.txt'

function Write-Note($Message) {
    [Console]::Error.WriteLine($Message)
}

function Fail($Message) {
    Write-Note "  [!!] $Message"
    exit 1
}

function Test-SnowLumaRoot($Path) {
    if (-not $Path) { return $false }
    $node = Test-Path -LiteralPath (Join-Path $Path 'node.exe') -PathType Leaf
    $entry = Test-Path -LiteralPath (Join-Path $Path 'index.mjs') -PathType Leaf
    return ($node -and $entry)
}

function Resolve-Candidate($Value) {
    if (-not $Value) { return $null }
    try { $full = [IO.Path]::GetFullPath($Value) } catch { return $null }
    if (Test-Path -LiteralPath $full -PathType Leaf) { $full = Split-Path -Parent $full }
    return $full
}

function Find-SnowLumaArchive() {
    $dirs = New-Object System.Collections.Generic.List[string]
    $dirs.Add($rootPath)
    $dirs.Add((Join-Path $rootPath 'downloads'))
    $dirs.Add((Join-Path $rootPath 'tools'))
    $dirs.Add((Split-Path -Parent $rootPath))
    if ($env:USERPROFILE) { $dirs.Add((Join-Path $env:USERPROFILE 'Downloads')) }
    $foundArchives = New-Object System.Collections.Generic.List[object]
    foreach ($dir in $dirs) {
        if (-not (Test-Path -LiteralPath $dir -PathType Container)) { continue }
        Get-ChildItem -LiteralPath $dir -File -Filter '*.zip' -ErrorAction SilentlyContinue |
            Where-Object { [regex]::IsMatch($_.Name, $zipPattern) } |
            ForEach-Object { $foundArchives.Add($_) }
    }
    if ($foundArchives.Count -eq 0) { return $null }
    return ($foundArchives | Sort-Object LastWriteTime -Descending | Select-Object -First 1)
}

function Copy-SnowLumaRuntime($Source, $Target) {
    if (-not (Test-Path -LiteralPath $Target -PathType Container)) {
        New-Item -ItemType Directory -Path $Target -Force | Out-Null
    }
    Get-ChildItem -LiteralPath $Source -Force | ForEach-Object {
        $dest = Join-Path $Target $_.Name
        if (($_.Name -eq 'config' -or $_.Name -eq 'data') -and (Test-Path -LiteralPath $dest)) { return }
        if ($_.PSIsContainer) {
            if (-not (Test-Path -LiteralPath $dest -PathType Container)) {
                New-Item -ItemType Directory -Path $dest -Force | Out-Null
            }
            Get-ChildItem -LiteralPath $_.FullName -Force | ForEach-Object {
                Copy-Item -LiteralPath $_.FullName -Destination $dest -Recurse -Force -ErrorAction Stop
            }
        } else {
            Copy-Item -LiteralPath $_.FullName -Destination $dest -Force -ErrorAction Stop
        }
    }
}

function Expand-SnowLumaArchive($Archive) {
    $target = Join-Path $rootPath 'snowluma'
    $temp = Join-Path ([IO.Path]::GetTempPath()) ('qqbot-snowluma-' + [guid]::NewGuid().ToString('N'))
    $errorMessage = $null
    New-Item -ItemType Directory -Path $temp -Force | Out-Null
    try {
        Write-Note "  [deps] Extracting SnowLuma archive: $($Archive.FullName)"
        Expand-Archive -LiteralPath $Archive.FullName -DestinationPath $temp -Force -ErrorAction Stop
        $entry = Get-ChildItem -LiteralPath $temp -Filter 'index.mjs' -File -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $entry) {
            $errorMessage = "SnowLuma archive does not contain index.mjs: $($Archive.FullName)"
        } else {
            $source = $entry.Directory.FullName
            if (-not (Test-SnowLumaRoot $source)) {
                $errorMessage = "SnowLuma archive does not contain node.exe next to index.mjs: $($Archive.FullName)"
            } else {
                Copy-SnowLumaRuntime $source $target
                if (-not (Test-SnowLumaRoot $target)) {
                    $errorMessage = "SnowLuma archive was extracted but node.exe/index.mjs are still missing: $target"
                } else {
                    Write-Note "  [OK] SnowLuma extracted to: $target"
                }
            }
        }
    } catch {
        $errorMessage = "Could not extract SnowLuma archive $($Archive.FullName): $($_.Exception.Message)"
    } finally {
        Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
    }
    if ($errorMessage) { Fail $errorMessage }
    return $target
}

$tried = New-Object System.Collections.Generic.List[string]
$snowHome = $null

$candidates = New-Object System.Collections.Generic.List[string]
if ($SnowLumaPath) { $candidates.Add($SnowLumaPath) }
if ($env:SNOWLUMA_HOME) { $candidates.Add($env:SNOWLUMA_HOME) }
if ($env:QQBOT_SNOWLUMA) { $candidates.Add($env:QQBOT_SNOWLUMA) }
$candidates.Add((Join-Path $rootPath 'snowluma'))
$candidates.Add((Join-Path (Split-Path -Parent $rootPath) 'SnowLuma'))
$candidates.Add((Join-Path (Split-Path -Parent $rootPath) 'snowluma'))

foreach ($candidate in $candidates) {
    $resolved = Resolve-Candidate $candidate
    if (-not $resolved) { continue }
    $tried.Add($resolved)
    if (Test-SnowLumaRoot $resolved) { $snowHome = $resolved; break }
}

if (-not $snowHome) {
    $archive = Find-SnowLumaArchive
    if ($archive) { $snowHome = Expand-SnowLumaArchive $archive }
}

if (-not $snowHome) {
    Write-Note '  [!!] SnowLuma runtime is missing.'
    Write-Note '  Tried:'
    foreach ($path in $tried) { Write-Note "    - $path" }
    Write-Note '  Set SNOWLUMA_HOME (or QQBOT_SNOWLUMA) to the SnowLuma folder, pass -SnowLumaPath,'
    Write-Note "  or place SnowLuma-v$version-win-x64.zip in the repo root, downloads, tools, parent, or %USERPROFILE%\Downloads."
    exit 1
}

$logDir = Join-Path $rootPath 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
[IO.File]::WriteAllText($pathFile, $snowHome, (New-Object System.Text.UTF8Encoding($false)))
$ansiPathFile = Join-Path $logDir 'snowluma-home.ansi.txt'
[IO.File]::WriteAllText($ansiPathFile, $snowHome, [Text.Encoding]::Default)
Write-Output $snowHome
