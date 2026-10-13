[CmdletBinding()]
param(
    [string]$ProjectRoot,
    [switch]$PlanOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "release-common.ps1")

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}

$createdStage = $false
$createdPartialZip = $false
$createdPartialSidecar = $false
$createdZip = $false
$createdSidecar = $false
try {
    $root = (Resolve-Path -LiteralPath $ProjectRoot).Path.TrimEnd('\')
    $headOutput = @(& git -C $root rev-parse --verify HEAD)
    if ($LASTEXITCODE -ne 0 -or $headOutput.Count -ne 1 -or
        $headOutput[0] -cnotmatch '^[0-9a-f]{40}$') {
        throw "SOURCE_HEAD_UNAVAILABLE"
    }
    $bundleHead = $headOutput[0]
    & git -C $root diff --quiet --exit-code
    if ($LASTEXITCODE -ne 0) { throw "SOURCE_WORKTREE_DIRTY" }
    & git -C $root diff --cached --quiet --exit-code
    if ($LASTEXITCODE -ne 0) { throw "SOURCE_INDEX_DIRTY" }
    $untracked = @(& git -C $root ls-files --others --exclude-standard)
    if ($LASTEXITCODE -ne 0 -or $untracked.Count -ne 0) {
        throw "SOURCE_UNTRACKED_FILES"
    }

    $versionMatch = [regex]::Match(
        (Get-Content -LiteralPath (Join-Path $root "app\version.py") -Raw -Encoding UTF8),
        '(?m)^VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"\s*$'
    )
    if (-not $versionMatch.Success) { throw "RELEASE_VERSION_INVALID" }
    $version = $versionMatch.Groups[1].Value
    $lock = Read-JevJson (Join-Path $root "packaging\transfer\bundle-release.json")
    if ($lock.schema_version -ne 1) { throw "BUNDLE_SCHEMA_MISMATCH" }
    Assert-JevVersion ([string]$lock.version)
    if ([string]$lock.version -cne $version) { throw "BUNDLE_VERSION_MISMATCH" }
    $applicationHead = [string]$lock.application_source_head
    $manifestHash = [string]$lock.application_manifest_sha256
    if ($applicationHead -cnotmatch '^[0-9a-f]{40}$') { throw "BUNDLE_SOURCE_ID_INVALID" }
    if ($manifestHash -cnotmatch '^[0-9a-f]{64}$') { throw "BUNDLE_MANIFEST_ID_INVALID" }
    & git -C $root cat-file -e "$applicationHead^{commit}"
    if ($LASTEXITCODE -ne 0) { throw "BUNDLE_SOURCE_COMMIT_MISSING" }

    $bundleName = "Jev-Chat-Assistant-$version-win-x64"
    $sourcePackage = Join-Path $root "dist\releases\$version\jev-chat-windows"
    $stageRoot = Join-Path $root "build\transfer-staging\$bundleName"
    $outputRoot = Join-Path $root "dist\transfer"
    $zipPath = Join-Path $outputRoot "$bundleName.zip"
    $sidecarPath = "$zipPath.sha256.txt"
    $partialZip = Join-Path $outputRoot "$bundleName.partial.zip"
    $partialSidecar = "$sidecarPath.partial"
    $conflicts = [ordered]@{
        BUNDLE_STAGE_ALREADY_EXISTS = $stageRoot
        BUNDLE_ZIP_ALREADY_EXISTS = $zipPath
        BUNDLE_SIDECAR_ALREADY_EXISTS = $sidecarPath
        BUNDLE_PARTIAL_ZIP_ALREADY_EXISTS = $partialZip
        BUNDLE_PARTIAL_SIDECAR_ALREADY_EXISTS = $partialSidecar
    }
    foreach ($code in $conflicts.Keys) {
        if (Test-Path -LiteralPath $conflicts[$code] -ErrorAction SilentlyContinue) { throw $code }
    }

    $verified = Assert-JevManifest $sourcePackage $version
    if ([string]$verified.Manifest.source_head -cne $applicationHead) {
        throw "BUNDLE_SOURCE_MISMATCH"
    }
    if ((Get-JevSha256 $verified.ManifestPath) -cne $manifestHash) {
        throw "BUNDLE_MANIFEST_MISMATCH"
    }
    $outerFiles = @("Install-Jev.cmd", "安装说明.txt", "BUNDLE-INFO.txt",
                    "scripts/install-release.ps1", "scripts/release-common.ps1")
    $copies = [ordered]@{
        "Install-Jev.cmd" = Join-Path $root "packaging\transfer\Install-Jev.cmd"
        "安装说明.txt" = Join-Path $root "packaging\transfer\安装说明.txt"
        "scripts/install-release.ps1" = Join-Path $root "scripts\install-release.ps1"
        "scripts/release-common.ps1" = Join-Path $root "scripts\release-common.ps1"
    }
    foreach ($source in $copies.Values) {
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
            throw "BUNDLE_INPUT_MISSING: $source"
        }
    }
    # Compress-Archive silently omits Hidden entries, including descendants of
    # hidden directories. Reject those inputs without changing their attributes.
    $inputPaths = @($copies.Values) + @($sourcePackage)
    $inputItems = @(Get-Item -LiteralPath $inputPaths -Force) +
        @(Get-ChildItem -LiteralPath $sourcePackage -Recurse -Force)
    foreach ($item in $inputItems) {
        if ($item.Attributes -band [IO.FileAttributes]::Hidden) {
            throw "BUNDLE_UNSUPPORTED_ATTRIBUTES: Hidden: $($item.FullName)"
        }
    }
    $details = "version=$version application_head=$applicationHead bundle_head=$bundleHead manifest_sha256=$manifestHash stage=$stageRoot zip=$zipPath"
    if ($PlanOnly) {
        Write-Output "PLAN_OK $details"
        exit 0
    }

    New-Item -ItemType Directory -Path (Split-Path -Parent $stageRoot) -Force | Out-Null
    New-Item -ItemType Directory -Path $stageRoot | Out-Null
    $createdStage = $true
    $stagedPackage = Join-Path $stageRoot "package\jev-chat-windows"
    New-Item -ItemType Directory -Path $stagedPackage -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $stageRoot "scripts") | Out-Null
    foreach ($relative in $copies.Keys) {
        Copy-Item -LiteralPath $copies[$relative] -Destination (Join-Path $stageRoot $relative)
    }
    Get-ChildItem -LiteralPath $verified.PackageRoot -Force |
        Copy-Item -Destination $stagedPackage -Recurse
    $info = @(
        "schema_version=1", "product=Jev Chat Assistant Personal", "version=$version",
        "architecture=win-x64", "application_source_head=$applicationHead",
        "bundle_tool_source_head=$bundleHead", "application_manifest_sha256=$manifestHash",
        "application_declared_file_count=$(@($verified.Manifest.files).Count)",
        "contains_credentials=no", "contains_user_config=no", "contains_chat_or_analysis=no",
        "hash_scope=transport_integrity_not_code_signing"
    )
    [IO.File]::WriteAllText((Join-Path $stageRoot "BUNDLE-INFO.txt"),
                          (($info -join "`r`n") + "`r`n"), [Text.Encoding]::ASCII)
    $staged = Assert-JevManifest $stagedPackage $version
    if ((Get-JevSha256 $staged.ManifestPath) -cne $manifestHash) {
        throw "BUNDLE_STAGED_MANIFEST_MISMATCH"
    }
    $packagePrefix = "package/jev-chat-windows/"
    $forbiddenSegments = @(".git", ".venv", ".build-venv", "__pycache__", "config.json",
                           "install-state.json", "rollback-release.ps1")
    $allowedDirectories = @("scripts", "package", "package/jev-chat-windows",
                            "${packagePrefix}_internal")
    $expectedFiles = [Collections.Generic.Dictionary[string,object]]::new([StringComparer]::Ordinal)
    $expectedDirectories = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $expectedDirectories.Add("$bundleName/") | Out-Null
    foreach ($item in Get-ChildItem -LiteralPath $stageRoot -Recurse -Force) {
        $relative = $item.FullName.Substring($stageRoot.Length).TrimStart('\').Replace('\', '/')
        if ($item.Attributes -band [IO.FileAttributes]::Hidden) {
            throw "BUNDLE_UNSUPPORTED_ATTRIBUTES: Hidden: $relative"
        }
        if ($item.PSIsContainer) {
            $allowed = $allowedDirectories -ccontains $relative -or
                $relative.StartsWith("${packagePrefix}_internal/", [StringComparison]::Ordinal)
        }
        else {
            $allowed = $outerFiles -ccontains $relative -or
                $relative -ceq "${packagePrefix}jev-chat-windows.exe" -or
                $relative -ceq "${packagePrefix}release-manifest.json" -or
                $relative.StartsWith("${packagePrefix}_internal/", [StringComparison]::Ordinal)
        }
        foreach ($segment in ($relative -split '/')) {
            if ($forbiddenSegments -contains $segment) { $allowed = $false }
        }
        if (-not $allowed) { throw "BUNDLE_FILE_NOT_ALLOWLISTED: $relative" }
        if ($item.PSIsContainer) {
            $expectedDirectories.Add("$bundleName/$relative/") | Out-Null
        }
        else {
            $expectedFiles.Add("$bundleName/$relative", [pscustomobject]@{
                Size = [int64]$item.Length
                Sha256 = Get-JevSha256 $item.FullName
            })
        }
    }

    New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
    $createdPartialZip = $true
    Compress-Archive -LiteralPath $stageRoot -DestinationPath $partialZip
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    # Windows PowerShell 5.1 can emit backslashes in ZIP names. Canonicalize
    # entries so other ZIP readers see the same portable directory structure.
    $archive = [IO.Compression.ZipFile]::Open($partialZip, [IO.Compression.ZipArchiveMode]::Update)
    try {
        foreach ($entry in @($archive.Entries)) {
            if ($entry.FullName.Contains('\')) {
                $replacement = $archive.CreateEntry($entry.FullName.Replace('\', '/'))
                $replacement.LastWriteTime = $entry.LastWriteTime
                $inputStream = $entry.Open()
                try {
                    $outputStream = $replacement.Open()
                    try { $inputStream.CopyTo($outputStream) }
                    finally { $outputStream.Dispose() }
                }
                finally { $inputStream.Dispose() }
                $entry.Delete()
            }
        }
    }
    finally { $archive.Dispose() }
    $archive = [IO.Compression.ZipFile]::OpenRead($partialZip)
    try {
        if ($archive.Entries.Count -eq 0) { throw "BUNDLE_ARCHIVE_EMPTY" }
        $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        foreach ($entry in $archive.Entries) {
            if (-not $entry.FullName.StartsWith("$bundleName/", [StringComparison]::Ordinal)) {
                throw "BUNDLE_ARCHIVE_ROOT_MISMATCH"
            }
            if ($entry.FullName.EndsWith('/')) {
                if (-not $expectedDirectories.Contains($entry.FullName) -or $entry.Length -ne 0) {
                    throw "BUNDLE_ARCHIVE_FILE_SET_MISMATCH"
                }
                continue
            }
            if (-not $expectedFiles.ContainsKey($entry.FullName) -or -not $seen.Add($entry.FullName)) {
                throw "BUNDLE_ARCHIVE_FILE_SET_MISMATCH"
            }
        }
        if ($seen.Count -ne $expectedFiles.Count) { throw "BUNDLE_ARCHIVE_FILE_SET_MISMATCH" }
        # Validate bytes independently from compression and ZIP-name normalization,
        # against the verified staging snapshot, before either final file exists.
        foreach ($entry in $archive.Entries) {
            if ($entry.FullName.EndsWith('/')) { continue }
            $expected = $expectedFiles[$entry.FullName]
            if ($entry.Length -ne $expected.Size) { throw "BUNDLE_ARCHIVE_HASH_MISMATCH" }
            $hasher = [Security.Cryptography.SHA256]::Create()
            try {
                $stream = $entry.Open()
                try {
                    $entryHash = [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-', '').ToLowerInvariant()
                }
                finally { $stream.Dispose() }
            }
            finally { $hasher.Dispose() }
            if ($entryHash -cne $expected.Sha256) { throw "BUNDLE_ARCHIVE_HASH_MISMATCH" }
        }
    }
    finally { $archive.Dispose() }
    $zipSha256 = Get-JevSha256 $partialZip
    $createdPartialSidecar = $true
    [IO.File]::WriteAllText(
        $partialSidecar,
        "$zipSha256  $([IO.Path]::GetFileName($zipPath))`r`n",
        [Text.Encoding]::ASCII
    )
    Move-Item -LiteralPath $partialZip -Destination $zipPath
    $createdPartialZip = $false
    $createdZip = $true
    Move-Item -LiteralPath $partialSidecar -Destination $sidecarPath
    $createdPartialSidecar = $false
    $createdSidecar = $true
    Write-Output "BUNDLE_OK $details zip_sha256=$zipSha256"
}
catch {
    $failure = $_.Exception.Message
    # Only invocation-owned paths are eligible for cleanup; conflict paths survive.
    if ($createdPartialZip -and (Test-Path -LiteralPath $partialZip)) {
        Remove-Item -LiteralPath $partialZip -Force
    }
    if ($createdPartialSidecar -and (Test-Path -LiteralPath $partialSidecar)) {
        Remove-Item -LiteralPath $partialSidecar -Force
    }
    if ($createdZip -and -not $createdSidecar -and (Test-Path -LiteralPath $zipPath)) {
        Remove-Item -LiteralPath $zipPath -Force
    }
    if ($createdStage -and (Test-Path -LiteralPath $stageRoot)) {
        Remove-Item -LiteralPath $stageRoot -Recurse -Force
    }
    # Avoid host-width formatting splitting the stable diagnostic code.
    [Console]::Error.WriteLine("BUNDLE_FAILED: " + $failure)
    exit 1
}
