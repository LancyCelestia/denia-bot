#Requires -Version 5.1
<#
.SYNOPSIS
    为 gsuid_core 开放局域网访问（仅本子网，入站 TCP 8765）。

.DESCRIPTION
    仅用于 Windows 本机开发；生产 Linux bot 服务器不需要本脚本。

    gsuid_core 的 WebConsole 默认只监听 127.0.0.1。要在局域网打开
    http://<本机局域网IP>:8765/app，需要：
      1. core 配置 HOST=0.0.0.0（安装脚本/README 已说明）；
      2. Windows 防火墙放行入站 8765。

    本脚本只放行 LocalSubnet，不会把 8765 暴露到公网；需要管理员权限，
    未提权时会自动弹出 UAC。重复运行会替换同名规则。

.NOTES
    WebConsole 仍受登录密码保护；WS 连接由 core 的 WS_TOKEN 保护。
    不要把规则改成 RemoteAddress=Any。
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$RuleName = 'QQBot gsuid_core 8765 (LAN)'
$Port = 8765

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)

if (-not $isAdmin) {
    Write-Host '需要管理员权限，正在请求提权...' -ForegroundColor Yellow
    $argList = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', ('"{0}"' -f $PSCommandPath)
    )
    $proc = Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $argList -Wait -PassThru
    exit $proc.ExitCode
}

Write-Host "配置 Windows 防火墙：入站 TCP $Port，仅限本地子网 ..." -ForegroundColor Cyan
$existing = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
if ($existing) {
    $existing | Remove-NetFirewallRule
    Write-Host '  已替换旧规则' -ForegroundColor DarkGray
}

New-NetFirewallRule `
    -DisplayName $RuleName `
    -Description 'denia-bot gsuid_core WebConsole/WS，仅本地子网' `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort $Port `
    -Profile Private,Public `
    -RemoteAddress LocalSubnet | Out-Null

$rule = Get-NetFirewallRule -DisplayName $RuleName
Write-Host "  [OK] $($rule.DisplayName)  Enabled=$($rule.Enabled)  Profile=$($rule.Profile)" -ForegroundColor Green

Write-Host ''
Write-Host '局域网访问地址（需先启动 core）：' -ForegroundColor Cyan
Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
    ForEach-Object {
        Write-Host ("  http://{0}:{1}/app  ({2})" -f $_.IPAddress, $Port, $_.InterfaceAlias)
    }

Write-Host ''
Write-Host '安全提醒：只在本子网使用；WebConsole 首次注册后请设置强密码，' -ForegroundColor Yellow
Write-Host '         WS_TOKEN 必须非空（core 与 NoneBot .env 保持一致）。' -ForegroundColor Yellow
