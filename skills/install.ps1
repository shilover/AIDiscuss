#requires -Version 5.1
<#
install.ps1  把 AIDiscuss skill 挂到本机各 AI agent 的 skill 目录。

做三件事：
  1. 写入 skills/aidiscuss/runtime.conf（项目根 + 解释器路径）
  2. 在 ~/.agents/skills/aidiscuss 建立 Junction
  3. 在 ~/.claude/skills/aidiscuss 建立 Junction

Junction 只是链接，编辑 D:\UEProject\AIDiscuss\skills\aidiscuss 下的文件即刻生效。
#>
[CmdletBinding()]
param(
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$skillSource = Join-Path $PSScriptRoot 'aidiscuss'
if (-not (Test-Path -LiteralPath (Join-Path $skillSource 'SKILL.md'))) {
    throw "找不到 skill 源目录: $skillSource"
}

$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = 'python' }

# 各 agent 的 skill 根目录
$roots = @(
    (Join-Path $HOME '.agents\skills'),
    (Join-Path $HOME '.claude\skills')
)

if ($Uninstall) {
    foreach ($root in $roots) {
        $link = Join-Path $root 'aidiscuss'
        if (Test-Path -LiteralPath $link) {
            & cmd /c rmdir "$link" | Out-Null
            Write-Host "removed  $link"
        }
    }
    return
}

# 1) runtime.conf
$confPath = Join-Path $skillSource 'runtime.conf'
$confLines = @(
    '# AIDiscuss skill 运行时配置。由 skills/install.ps1 生成，可手工修改。',
    "# project: AIDiscuss 项目根（含 app/cli.py）",
    "# python:  解释器路径（含 pydantic/typer/rich/httpx 的环境）",
    "project=$projectRoot",
    "python=$python"
)
[System.IO.File]::WriteAllText($confPath, ($confLines -join "`r`n") + "`r`n", (New-Object System.Text.UTF8Encoding($false)))
Write-Host "wrote    $confPath"

# 2) 建 Junction
foreach ($root in $roots) {
    New-Item -ItemType Directory -Force -Path $root | Out-Null
    $link = Join-Path $root 'aidiscuss'

    if (Test-Path -LiteralPath $link) {
        $item = Get-Item -LiteralPath $link -Force
        if ($item.LinkType -eq 'Junction' -and $item.Target -contains $skillSource) {
            Write-Host "ok       $link  (已指向 $skillSource)"
            continue
        }
        & cmd /c rmdir "$link" | Out-Null
        Write-Host "replaced $link"
    }

    New-Item -ItemType Junction -Path $link -Target $skillSource | Out-Null
    Write-Host "linked   $link -> $skillSource"
}

Write-Host ''
Write-Host '安装完成。验证：'
Write-Host '  powershell -NoProfile -ExecutionPolicy Bypass -File "$HOME\.agents\skills\aidiscuss\scripts\aidiscuss.ps1" models'
Write-Host '在 Claude Code / dsh 里说用 aidiscuss 讨论这个改动即可触发。'