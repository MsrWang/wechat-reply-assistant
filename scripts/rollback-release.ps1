[CmdletBinding()]
param(
    [string]$DesktopPath,
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "release-common.ps1")

$shortcutBackup = $null
$shortcutExisted = $false
$shortcutChanged = $false
try {
    $paths = Get-JevInstallPaths -DesktopPath $DesktopPath
    $state = Read-JevJson $paths.State
    if ($state.schema_version -ne 1 -or $null -eq $state.current -or $null -eq $state.previous) {
        throw "NO_PREVIOUS_RELEASE"
    }
    $previousVersion = [string]$state.previous.version
    Assert-JevVersion $previousVersion
    $previousPath = Join-Path $paths.Releases $previousVersion
    $verified = Assert-JevManifest $previousPath $previousVersion
    if ($verified.ManifestSha256 -cne [string]$state.previous.manifest_sha256) {
        throw "PREVIOUS_MANIFEST_MISMATCH"
    }
    $target = Join-Path $previousPath "jev-chat-windows.exe"
    if ($WhatIf) {
        Write-Output "WHATIF rollback current=$($state.current.version) target=$previousVersion"
        Write-Output "WHATIF shortcut=$($paths.Shortcut)"
        exit 0
    }

    if (Get-Process -Name "jev-chat-windows" -ErrorAction SilentlyContinue) {
        throw "JEV_PROCESS_RUNNING"
    }
    $confirmation = Read-Host "Type ROLLBACK to switch to the verified previous release"
    if ($confirmation -cne "ROLLBACK") {
        throw "ROLLBACK_NOT_CONFIRMED"
    }
    $newState = [ordered]@{
        schema_version = 1
        current = $state.previous
        previous = $state.current
    }
    if (Test-Path -LiteralPath $paths.Shortcut -PathType Leaf) {
        $shortcutExisted = $true
        $shortcutBackup = "$($paths.Shortcut).backup.$([Guid]::NewGuid().ToString('N'))"
        Copy-Item -LiteralPath $paths.Shortcut -Destination $shortcutBackup
    }
    try {
        New-JevShortcutFile $target $paths.Shortcut
        $shortcutChanged = $true
        Write-JevJsonAtomic $paths.State $newState
    }
    catch {
        if ($shortcutChanged -and (Test-Path -LiteralPath $paths.Shortcut)) {
            Remove-Item -LiteralPath $paths.Shortcut -Force
        }
        if ($shortcutExisted -and (Test-Path -LiteralPath $shortcutBackup)) {
            Move-Item -LiteralPath $shortcutBackup -Destination $paths.Shortcut -Force
            $shortcutBackup = $null
        }
        throw
    }
    if ($shortcutBackup -and (Test-Path -LiteralPath $shortcutBackup)) {
        Remove-Item -LiteralPath $shortcutBackup -Force
        $shortcutBackup = $null
    }
    Write-Output "ROLLBACK_OK version=$previousVersion manifest_sha256=$($verified.ManifestSha256)"
}
catch {
    if ($shortcutBackup -and (Test-Path -LiteralPath $shortcutBackup)) {
        Remove-Item -LiteralPath $shortcutBackup -Force
    }
    Write-Error ("ROLLBACK_FAILED: " + $_.Exception.Message)
    exit 1
}
