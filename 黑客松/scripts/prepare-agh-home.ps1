$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent))
$instanceHome = Join-Path $projectRoot '.runtime/agh'
$currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$systemSid = New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
$inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
$propagate = [Security.AccessControl.PropagationFlags]::None
$allow = [Security.AccessControl.AccessControlType]::Allow
foreach ($target in @($instanceHome, (Join-Path $instanceHome 'profiles'), (Join-Path $instanceHome 'profiles/local-dev'))) {
    $absolute = [IO.Path]::GetFullPath($target)
    if (!$absolute.StartsWith($projectRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Target is outside project' }
    if (!(Test-Path -LiteralPath $absolute)) { New-Item -ItemType Directory -Path $absolute -Force | Out-Null }
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $acl.SetOwner($currentSid)
    foreach ($sid in @($currentSid, $systemSid)) {
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', $inherit, $propagate, $allow)
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $absolute -AclObject $acl
}
Write-Output 'AGH local home is private to the current Windows user and SYSTEM.'
