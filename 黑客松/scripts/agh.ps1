param([Parameter(ValueFromRemainingArguments=$true)][string[]]$CommandArgs)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$nodeDir = Join-Path $projectRoot '.tools/node-v24.10.0-win-x64'
$env:PATH = "$nodeDir;$(Join-Path $projectRoot '.tools/python');$env:PATH"
$env:AGH_HOME = Join-Path $projectRoot '.runtime/agh'
$env:AGNES_PROFILE = 'local-dev'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$env:TEMP = Join-Path $projectRoot '.cache/temp'
$env:TMP = $env:TEMP
Push-Location $projectRoot
try {
    & (Join-Path $nodeDir 'node.exe') (Join-Path $projectRoot '.tools/agnes-harness/packages/cli/dist/local/agnes.mjs') @CommandArgs
    exit $LASTEXITCODE
} finally { Pop-Location }
