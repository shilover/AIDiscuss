#requires -Version 5.1
<#
aidiscuss.ps1  AIDiscuss 多模型代码方案讨论器的 skill 包装器。

用法：
  aidiscuss.ps1 doctor
  aidiscuss.ps1 models
  aidiscuss.ps1 discuss --repo <仓库> --file <需求.md> --rounds 2 --out <目录>
  aidiscuss.ps1 start   --repo <仓库> --file <需求.md> --rounds 2 --out <目录>

start = 后台运行 discuss 并立即返回，避免 agent 的命令超时。

注意：本脚本故意不使用 param 块。--repo / --out 这类 CLI 参数如果交给
PowerShell 参数绑定，会被当成 -repo / -out 解析（-out 还会和 -OutVariable
产生歧义错误），所以统一从 $args 里手工取。
#>

$aidiscussArgs = @($args)
if ($aidiscussArgs.Count -eq 0) { $aidiscussArgs = @('discuss') }
$Command = [string]$aidiscussArgs[0]
$Rest = @($aidiscussArgs | Select-Object -Skip 1)

$ErrorActionPreference = 'Stop'
$env:NO_COLOR = '1'

function Read-RuntimeConf {
    $conf = @{}
    $path = Join-Path $PSScriptRoot '..\runtime.conf'
    if (Test-Path -LiteralPath $path) {
        foreach ($line in (Get-Content -LiteralPath $path -Encoding UTF8)) {
            if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$') {
                $conf[$Matches[1]] = $Matches[2]
            }
        }
    }
    return $conf
}

function Resolve-ProjectRoot {
    param([hashtable]$Conf)

    $candidates = @()
    if ($env:AIDISCUSS_HOME) { $candidates += $env:AIDISCUSS_HOME }
    if ($Conf['project']) { $candidates += $Conf['project'] }
    $candidates += (Join-Path $PSScriptRoot '..\..\..')
    $candidates += 'D:\UEProject\AIDiscuss'
    $candidates += (Join-Path $HOME 'AIDiscuss')

    foreach ($candidate in $candidates) {
        if (-not $candidate) { continue }
        try { $full = (Resolve-Path -LiteralPath $candidate -ErrorAction Stop).Path }
        catch { continue }
        if (Test-Path -LiteralPath (Join-Path $full 'app\cli.py')) { return $full }
    }
    throw 'aidiscuss: 找不到 AIDiscuss 项目根。请设置 AIDISCUSS_HOME，或修正 runtime.conf 里的 project=。'
}

function Resolve-Python {
    param([hashtable]$Conf, [string]$ProjectRoot)

    if ($Conf['python'] -and (Test-Path -LiteralPath $Conf['python'])) { return $Conf['python'] }
    $venv = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venv) { return $venv }
    $found = Get-Command python -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    throw 'aidiscuss: 找不到可用的 Python 解释器（.venv\Scripts\python.exe 或 PATH 里的 python）。'
}

function Get-OptionValue {
    param([string[]]$Arguments, [string]$Name)

    for ($i = 0; $i -lt $Arguments.Count; $i++) {
        $arg = $Arguments[$i]
        if ($arg -eq $Name) {
            if ($i + 1 -ge $Arguments.Count) { throw "aidiscuss: $Name 缺少取值。" }
            return $Arguments[$i + 1]
        }
        if ($arg -like "$Name=*") { return $arg.Substring($Name.Length + 1) }
    }
    return $null
}

$conf = Read-RuntimeConf
$projectRoot = Resolve-ProjectRoot -Conf $conf
$python = Resolve-Python -Conf $conf -ProjectRoot $projectRoot

if ($Command -eq 'config') {
    $confPath = Join-Path $PSScriptRoot '..\runtime.conf'
    Write-Output "conf    = $((Resolve-Path -LiteralPath $confPath -ErrorAction SilentlyContinue).Path)"
    Write-Output "project = $projectRoot"
    Write-Output "python  = $python"
    exit 0
}

function Invoke-Aidiscuss {
    param([string[]]$Arguments)

    Push-Location -LiteralPath $projectRoot
    try {
        & $python '-m' 'app.cli' @Arguments
        $code = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    exit $code
}

if ($Command -ne 'start') {
    Invoke-Aidiscuss -Arguments (@($Command) + $Rest)
}

# ---- start：后台运行 discuss ----
$outDir = Get-OptionValue -Arguments $Rest -Name '--out'
if (-not $outDir) {
    throw 'aidiscuss start: 必须用 --out <目录> 指定输出目录（后台运行需要先知道产物位置）。'
}

$outDir = [System.IO.Path]::GetFullPath($outDir)
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$stdoutLog = Join-Path $outDir 'run.out.log'
$stderrLog = Join-Path $outDir 'run.err.log'

$shell = (Get-Command pwsh -ErrorAction SilentlyContinue)
if (-not $shell) { $shell = Get-Command powershell -ErrorAction Stop }

$childArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath, 'discuss') + $Rest
$quoted = foreach ($a in $childArgs) {
    if ($a -match '[\s"]') { '"' + ($a -replace '"', '\"') + '"' } else { $a }
}

$proc = Start-Process -FilePath $shell.Source `
    -ArgumentList ($quoted -join ' ') `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -WindowStyle Hidden `
    -PassThru

Write-Output "AIDISCUSS_STARTED pid=$($proc.Id)"
Write-Output "AIDISCUSS_OUT $outDir"
Write-Output "AIDISCUSS_LOG $stdoutLog"
Write-Output "AIDISCUSS_ERR $stderrLog"
exit 0