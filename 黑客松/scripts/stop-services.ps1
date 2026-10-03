param([switch]$DashboardOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent))
$registryPath = Join-Path $projectRoot '.runtime/services.json'
if (!(Test-Path -LiteralPath $registryPath)) { exit 0 }
$registry = Get-Content -LiteralPath $registryPath -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($name in @('dashboard','agh-web')) {
    if ($DashboardOnly -and $name -ne 'dashboard') { continue }
    $entry = $registry.$name
    if (!$entry) { continue }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($entry.pid)"
    if (!$process) { continue }
    $expected = [IO.Path]::GetFullPath($entry.command[0])
    if (!$expected.StartsWith($projectRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Runtime is outside project' }
    if ($process.ExecutablePath -ne $expected) { throw "PID identity changed: $name" }
    $marker = if ($name -eq 'dashboard') { 'traffic_agent.dashboard' } else { 'agnes.mjs' }
    if (!$process.CommandLine.Contains($marker)) { throw "Command identity changed: $name" }
    Stop-Process -Id $entry.pid
    Write-Output "Stopped project service: $name"
}
