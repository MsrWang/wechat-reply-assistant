[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PackagePath,
    [Parameter(Mandatory = $true)][string]$ExpectedVersion,
    [string]$DesktopPath,
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "release-common.ps1")

$temporaryRelease = $null
$shortcutBackup = $null
$shortcutExisted = $false
$shortcutChanged = $false
try {
    $verified = Assert-JevManifest $PackagePath $ExpectedVersion
    $paths = Get-JevInstallPaths -DesktopPath $DesktopPath
    $destination = Join-Path $paths.Releases $ExpectedVersion
    $target = Join-Path $destination "jev-chat-windows.exe"
    if ($WhatIf) {
        $plannedPrevious = "<none>"
        if (Test-Path -LiteralPath $paths.State -PathType Leaf) {
            $plannedState = Read-JevJson $paths.State
            if ($plannedState.schema_version -ne 1) { throw "INSTALL_STATE_INVALID" }
            if ($null -ne $plannedState.current) {
                $plannedPrevious = [string]$plannedState.current.version
            }
        }
        Write-Output "WHATIF install version=$ExpectedVersion manifest_sha256=$($verified.ManifestSha256)"
        Write-Output "WHATIF transition current=$ExpectedVersion previous=$plannedPrevious"
        Write-Output "WHATIF release=$destination"
        Write-Output "WHATIF shortcut=$($paths.Shortcut)"
        $rollback = Join-Path $PSScriptRoot "rollback-release.ps1"
        if (Test-Path -LiteralPath $rollback -PathType Leaf) {
            Write-Output "WHATIF rollback=$rollback"
        }
        else {
            Write-Output "WHATIF rollback=<not bundled>"
        }
        exit 0
    }

    if (Get-Process -Name "jev-chat-windows" -ErrorAction SilentlyContinue) {
        throw "JEV_PROCESS_RUNNING"
    }
    Write-Output "PACKAGE_READY version=$ExpectedVersion manifest_sha256=$($verified.ManifestSha256)"
    Write-Output "Type INSTALL only after Windows Defender reports this exact package clean"
    $confirmation = Read-Host
    if ($confirmation -cne "INSTALL") {
        throw "INSTALL_NOT_CONFIRMED"
    }

    if (Test-Path -LiteralPath $destination) {
        $existing = Assert-JevManifest $destination $ExpectedVersion
        if ($existing.ManifestSha256 -cne $verified.ManifestSha256) {
            throw "RELEASE_EXISTS_MISMATCH"
        }
    }
    else {
        New-Item -ItemType Directory -Path $paths.Releases -Force | Out-Null
        $temporaryRelease = Join-Path $paths.Releases (".$ExpectedVersion.tmp." + [Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $temporaryRelease | Out-Null
        Get-ChildItem -LiteralPath $verified.PackageRoot -Force |
            Copy-Item -Destination $temporaryRelease -Recurse -Force
        $copied = Assert-JevManifest $temporaryRelease $ExpectedVersion
        if ($copied.ManifestSha256 -cne $verified.ManifestSha256) {
            throw "COPIED_PACKAGE_MISMATCH"
        }
        Move-Item -LiteralPath $temporaryRelease -Destination $destination
        $temporaryRelease = $null
    }

    $oldState = $null
    if (Test-Path -LiteralPath $paths.State -PathType Leaf) {
        $oldState = Read-JevJson $paths.State
        if ($oldState.schema_version -ne 1) { throw "INSTALL_STATE_INVALID" }
    }
    $newState = [ordered]@{
        schema_version = 1
        current = [ordered]@{
            version = $ExpectedVersion
            manifest_sha256 = $verified.ManifestSha256
        }
        previous = if ($null -eq $oldState) { $null } else { $oldState.current }
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
    Write-Output "INSTALL_OK version=$ExpectedVersion manifest_sha256=$($verified.ManifestSha256)"
}
catch {
    if ($temporaryRelease -and (Test-Path -LiteralPath $temporaryRelease)) {
        Remove-Item -LiteralPath $temporaryRelease -Recurse -Force
    }
    if ($shortcutBackup -and (Test-Path -LiteralPath $shortcutBackup)) {
        Remove-Item -LiteralPath $shortcutBackup -Force
    }
    Write-Error ("INSTALL_FAILED: " + $_.Exception.Message)
    exit 1
}
