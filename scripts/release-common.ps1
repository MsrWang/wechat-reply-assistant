Set-StrictMode -Version Latest

function Get-JevSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Read-JevJson {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "MISSING_JSON: $Path"
    }
    return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Assert-JevVersion {
    param([Parameter(Mandatory = $true)][string]$Version)
    if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
        throw "PACKAGE_VERSION_INVALID"
    }
}

function Assert-JevManifest {
    param(
        [Parameter(Mandatory = $true)][string]$PackagePath,
        [Parameter(Mandatory = $true)][string]$ExpectedVersion
    )

    Assert-JevVersion $ExpectedVersion
    $resolved = Resolve-Path -LiteralPath $PackagePath -ErrorAction Stop
    $root = $resolved.Path.TrimEnd('\')
    if (-not (Test-Path -LiteralPath $root -PathType Container)) {
        throw "PACKAGE_NOT_DIRECTORY"
    }
    $manifestPath = Join-Path $root "release-manifest.json"
    $manifest = Read-JevJson $manifestPath
    if ($manifest.schema_version -ne 1) {
        throw "PACKAGE_SCHEMA_MISMATCH"
    }
    if ([string]$manifest.version -cne $ExpectedVersion) {
        throw "PACKAGE_VERSION_MISMATCH"
    }
    if ([string]$manifest.source_head -notmatch '^[0-9a-f]{40}$') {
        throw "PACKAGE_SOURCE_ID_INVALID"
    }
    foreach ($name in @("requirements_lock_sha256", "requirements_build_lock_sha256")) {
        if ([string]$manifest.$name -notmatch '^[0-9a-f]{64}$') {
            throw "PACKAGE_LOCK_ID_INVALID"
        }
    }

    $declared = @{}
    foreach ($entry in @($manifest.files)) {
        $relative = [string]$entry.path
        $segments = @($relative -split '/')
        if ([string]::IsNullOrWhiteSpace($relative) -or
            [IO.Path]::IsPathRooted($relative) -or
            $relative.Contains('\') -or
            $segments -contains '..' -or
            $relative -eq 'release-manifest.json' -or
            $declared.ContainsKey($relative)) {
            throw "PACKAGE_PATH_INVALID"
        }
        $declared[$relative] = $entry
    }
    if ($declared.Count -eq 0) {
        throw "PACKAGE_FILE_LIST_EMPTY"
    }

    $actual = @{}
    foreach ($file in Get-ChildItem -LiteralPath $root -Recurse -File -Force) {
        $relative = $file.FullName.Substring($root.Length).TrimStart('\').Replace('\', '/')
        if ($relative -ne 'release-manifest.json') {
            $actual[$relative] = $file
        }
    }
    if ($actual.Count -ne $declared.Count) {
        throw "PACKAGE_FILE_SET_MISMATCH"
    }
    foreach ($relative in $declared.Keys) {
        if (-not $actual.ContainsKey($relative)) {
            throw "PACKAGE_FILE_SET_MISMATCH"
        }
        $entry = $declared[$relative]
        $file = $actual[$relative]
        if ([int64]$entry.size -ne [int64]$file.Length -or
            (Get-JevSha256 $file.FullName) -cne ([string]$entry.sha256).ToLowerInvariant()) {
            throw "PACKAGE_HASH_MISMATCH"
        }
    }

    return [pscustomobject]@{
        PackageRoot = $root
        Manifest = $manifest
        ManifestPath = $manifestPath
        ManifestSha256 = Get-JevSha256 $manifestPath
    }
}

function Get-JevInstallPaths {
    param([string]$DesktopPath)

    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        throw "USER_PATHS_UNAVAILABLE"
    }
    if ([string]::IsNullOrWhiteSpace($DesktopPath)) {
        $DesktopPath = [Environment]::GetFolderPath(
            [Environment+SpecialFolder]::DesktopDirectory
        )
    }
    if ([string]::IsNullOrWhiteSpace($DesktopPath)) {
        throw "DESKTOP_PATH_UNAVAILABLE"
    }
    $root = Join-Path $env:LOCALAPPDATA "JevChatPersonal"
    return [pscustomobject]@{
        Root = $root
        Releases = Join-Path $root "releases"
        State = Join-Path $root "install-state.json"
        Shortcut = Join-Path $DesktopPath "Jev Chat Assistant.lnk"
    }
}

function Write-JevJsonAtomic {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Value
    )
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $temporary = "$Path.tmp.$([Guid]::NewGuid().ToString('N'))"
    try {
        $Value | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporary -Encoding UTF8
        Move-Item -LiteralPath $temporary -Destination $Path -Force
    }
    finally {
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary -Force
        }
    }
}

function New-JevShortcutFile {
    param(
        [Parameter(Mandatory = $true)][string]$TargetPath,
        [Parameter(Mandatory = $true)][string]$ShortcutPath
    )
    if (-not (Test-Path -LiteralPath $TargetPath -PathType Leaf)) {
        throw "SHORTCUT_TARGET_MISSING"
    }
    $parent = Split-Path -Parent $ShortcutPath
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $temporary = "$ShortcutPath.tmp.$([Guid]::NewGuid().ToString('N')).lnk"
    try {
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($temporary)
        $shortcut.TargetPath = $TargetPath
        $shortcut.WorkingDirectory = Split-Path -Parent $TargetPath
        $shortcut.Description = "Jev Chat Assistant Personal"
        $shortcut.Save()
        Move-Item -LiteralPath $temporary -Destination $ShortcutPath -Force
    }
    finally {
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary -Force
        }
    }
}
