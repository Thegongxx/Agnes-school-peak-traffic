param([switch]$RuntimeOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$cacheRoot = Join-Path $projectRoot '.cache'
$toolsRoot = Join-Path $projectRoot '.tools'
New-Item -ItemType Directory -Force -Path $cacheRoot,$toolsRoot,(Join-Path $cacheRoot 'npm'),(Join-Path $cacheRoot 'pip') | Out-Null
$env:TEMP = Join-Path $cacheRoot 'temp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
function Fetch-File([string]$Url,[string]$File) {
    if (!(Test-Path -LiteralPath $File)) { Invoke-WebRequest -Uri $Url -OutFile $File }
}
$nodeZip = Join-Path $cacheRoot 'node-v24.10.0-win-x64.zip'
$nodeDir = Join-Path $toolsRoot 'node-v24.10.0-win-x64'
if (!(Test-Path -LiteralPath (Join-Path $nodeDir 'node.exe'))) {
    Fetch-File 'https://nodejs.org/dist/v24.10.0/node-v24.10.0-win-x64.zip' $nodeZip
    $nodeManifest = Join-Path $cacheRoot 'node-v24.10.0-SHASUMS256.txt'
    Fetch-File 'https://nodejs.org/dist/v24.10.0/SHASUMS256.txt' $nodeManifest
    $expected = ((Get-Content $nodeManifest | Where-Object { $_ -match ' node-v24.10.0-win-x64.zip$' }) -split '\s+')[0]
    if (!$expected -or (Get-FileHash $nodeZip -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) { throw 'Node SHA256 mismatch' }
    Expand-Archive -LiteralPath $nodeZip -DestinationPath $toolsRoot
}
$pythonDir = Join-Path $toolsRoot 'python'
if (!(Test-Path -LiteralPath (Join-Path $pythonDir 'python.exe'))) {
    $pythonZip = Join-Path $cacheRoot 'python-3.12.10-embed-amd64.zip'
    Fetch-File 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip' $pythonZip
    if ((Get-FileHash $pythonZip -Algorithm MD5).Hash.ToLowerInvariant() -ne 'fe8ef205f2e9c3ba44d0cf9954e1abd3') { throw 'Python archive checksum mismatch' }
    Expand-Archive -LiteralPath $pythonZip -DestinationPath $pythonDir
}
[IO.File]::WriteAllText((Join-Path $pythonDir 'python312._pth'), "python312.zip`n.`nLib/site-packages`n$projectRoot`nimport site`n", [Text.UTF8Encoding]::new($false))
$env:PATH = "$nodeDir;$pythonDir;$pythonDir\Scripts;$env:PATH"
$env:PIP_CACHE_DIR = Join-Path $cacheRoot 'pip'
$env:npm_config_cache = Join-Path $cacheRoot 'npm'
$pythonExe = Join-Path $pythonDir 'python.exe'
if (!(Test-Path -LiteralPath (Join-Path $pythonDir 'Lib\site-packages\pip'))) {
    $pipBootstrap = Join-Path $cacheRoot 'get-pip.py'
    Fetch-File 'https://bootstrap.pypa.io/get-pip.py' $pipBootstrap
    & $pythonExe $pipBootstrap --no-warn-script-location
    if ($LASTEXITCODE -ne 0) { throw 'Local pip installation failed' }
}
$pnpmRoot = Join-Path $toolsRoot 'pnpm'
if (!(Test-Path -LiteralPath (Join-Path $pnpmRoot 'node_modules\pnpm\bin\pnpm.cjs'))) {
    & (Join-Path $nodeDir 'npm.cmd') install --prefix $pnpmRoot --cache (Join-Path $cacheRoot 'npm') --ignore-scripts --no-audit --no-fund pnpm@10.34.5
    if ($LASTEXITCODE -ne 0) { throw 'Local pnpm installation failed' }
}
if (!$RuntimeOnly) {
    & $pythonExe -m pip install --no-warn-script-location --cache-dir (Join-Path $cacheRoot 'pip') -r (Join-Path $projectRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Project Python dependencies failed' }
}
& (Join-Path $nodeDir 'node.exe') --version
& $pythonExe --version
& (Join-Path $nodeDir 'node.exe') (Join-Path $pnpmRoot 'node_modules\pnpm\bin\pnpm.cjs') --version
