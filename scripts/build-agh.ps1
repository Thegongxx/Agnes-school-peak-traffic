$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$nodeDir = Join-Path $projectRoot '.tools/node-v24.10.0-win-x64'
$nodeExe = Join-Path $nodeDir 'node.exe'
$pythonExe = Join-Path $projectRoot '.tools/python/python.exe'
$headersDir = Join-Path $projectRoot '.tools/node-headers/24.10.0'
$cacheDir = Join-Path $projectRoot '.cache'
$env:PATH = "$nodeDir;$env:PATH"
$env:TEMP = Join-Path $cacheDir 'temp'
$env:TMP = $env:TEMP
$env:npm_config_cache = Join-Path $cacheDir 'npm'
if (!(Test-Path (Join-Path $headersDir 'include/node/node_api.h'))) {
    New-Item -ItemType Directory -Force -Path $headersDir,(Join-Path $headersDir 'x64') | Out-Null
    Invoke-WebRequest 'https://nodejs.org/dist/v24.10.0/node-v24.10.0-headers.tar.gz' -OutFile (Join-Path $cacheDir 'node-headers.tar.gz')
    tar -xzf (Join-Path $cacheDir 'node-headers.tar.gz') -C $headersDir --strip-components=1
    if ($LASTEXITCODE -ne 0) { throw 'Node header extraction failed' }
    Invoke-WebRequest 'https://nodejs.org/dist/v24.10.0/win-x64/node.lib' -OutFile (Join-Path $headersDir 'x64/node.lib')
    $checksums = Get-Content (Join-Path $cacheDir 'node-v24.10.0-SHASUMS256.txt')
    foreach ($entry in @(@('node-v24.10.0-headers.tar.gz',(Join-Path $cacheDir 'node-headers.tar.gz')), @('win-x64/node.lib',(Join-Path $headersDir 'x64/node.lib')))) {
        $expected = (($checksums | Where-Object { $_.EndsWith(' ' + $entry[0]) }) -split '\s+')[0]
        if (!$expected -or (Get-FileHash $entry[1] -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) { throw 'Node native resource SHA256 mismatch' }
    }
}
$compiler = Get-Content (Join-Path $projectRoot '.tools/compiler-env.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$env:AGNES_NODE_HEADERS = $headersDir
$env:VCToolsInstallDir = $compiler.vc_tools
$env:INCLUDE = $compiler.include
$env:LIB = $compiler.lib
$env:PATH = "$(Split-Path $compiler.cl);$nodeDir;$env:PATH"
$delayLib = Join-Path $compiler.vc_tools 'lib/x64/delayimp.lib'
if (!(Test-Path $delayLib)) {
    $delayWork = Join-Path $cacheDir 'delay-helper'
    New-Item -ItemType Directory -Force -Path $delayWork | Out-Null
    Push-Location $delayWork
    try {
        # Build the unmodified Microsoft helper using its supplied instructions.
        & $compiler.cl /nologo /c /O1 /Zl /W3 /std:c++17 (Join-Path $compiler.vc_tools 'include/delayhlp.cpp')
        if ($LASTEXITCODE -ne 0) { throw 'Microsoft delay-load helper compilation failed' }
        & (Join-Path (Split-Path $compiler.cl) 'lib.exe') /nologo "/OUT:$delayLib" 'delayhlp.obj'
        if ($LASTEXITCODE -ne 0) { throw 'Microsoft delay-load helper archive failed' }
    } finally { Pop-Location }
}
Push-Location (Join-Path $projectRoot '.tools/agnes-harness')
try {
    $ErrorActionPreference = 'Continue'
    & $nodeExe (Join-Path $projectRoot '.tools/pnpm/node_modules/pnpm/bin/pnpm.cjs') --filter '@agnes/cli' build:local 2>&1 | Tee-Object -FilePath (Join-Path $projectRoot '.runtime/agh-build.log')
    $ErrorActionPreference = 'Stop'
    if ($LASTEXITCODE -ne 0) { throw 'AGH build failed; see .runtime/agh-build.log' }
    & $nodeExe 'packages/cli/dist/local/agnes.mjs' --help
    if ($LASTEXITCODE -ne 0) { throw 'AGH CLI validation failed' }
} finally { Pop-Location }
Push-Location $projectRoot
try {
    & $nodeExe 'scripts/build-sdk.mjs'
    if ($LASTEXITCODE -ne 0) { throw 'Campus AGH SDK bridge build failed' }
} finally { Pop-Location }
