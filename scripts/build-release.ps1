[CmdletBinding()]
param(
    [string]$ProjectRoot,
    [string]$BuildPython,
    [switch]$PlanOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "release-common.ps1")

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}

try {
    $root = (Resolve-Path -LiteralPath $ProjectRoot -ErrorAction Stop).Path
    $runtimeLock = Join-Path $root "requirements.lock"
    $buildLock = Join-Path $root "requirements-build.lock"
    foreach ($required in @($runtimeLock, $buildLock)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            throw "MISSING_LOCK_FILE: $([IO.Path]::GetFileName($required))"
        }
    }

    $versionFile = Join-Path $root "app\version.py"
    $versionMatch = [regex]::Match(
        (Get-Content -LiteralPath $versionFile -Raw -Encoding UTF8),
        'VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"'
    )
    if (-not $versionMatch.Success) {
        throw "RELEASE_VERSION_INVALID"
    }
    $version = $versionMatch.Groups[1].Value
    $python = Join-Path $root ".build-venv\Scripts\python.exe"
    if (-not [string]::IsNullOrWhiteSpace($BuildPython)) {
        if (-not [IO.Path]::IsPathRooted($BuildPython) -or
            -not (Test-Path -LiteralPath $BuildPython -PathType Leaf) -or
            [IO.Path]::GetExtension($BuildPython) -ine ".exe") {
            throw "BUILD_PYTHON_PATH_INVALID"
        }
        $python = (Resolve-Path -LiteralPath $BuildPython -ErrorAction Stop).Path
    }
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "BUILD_ENV_MISSING: .build-venv"
    }
    $pythonVersion = (& $python -c "import platform; print(platform.python_version())").Trim()
    if ($LASTEXITCODE -ne 0 -or $pythonVersion -notmatch '^3\.11\.') {
        throw "BUILD_PYTHON_MUST_BE_3_11"
    }
    & $python (Join-Path $root "scripts\verify_locked_environment.py") $buildLock
    if ($LASTEXITCODE -ne 0) {
        throw "BUILD_ENV_DOES_NOT_MATCH_LOCK"
    }

    $head = (& git -C $root rev-parse --verify HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $head -notmatch '^[0-9a-f]{40}$') {
        throw "SOURCE_HEAD_UNAVAILABLE"
    }
    & git -C $root diff --quiet --exit-code
    if ($LASTEXITCODE -ne 0) { throw "SOURCE_WORKTREE_DIRTY" }
    & git -C $root diff --cached --quiet --exit-code
    if ($LASTEXITCODE -ne 0) { throw "SOURCE_INDEX_DIRTY" }
    $untracked = @(& git -C $root ls-files --others --exclude-standard)
    if ($LASTEXITCODE -ne 0 -or $untracked.Count -ne 0) {
        throw "SOURCE_UNTRACKED_FILES"
    }

    $releaseRoot = Join-Path $root "dist\releases\$version"
    $destination = Join-Path $releaseRoot "jev-chat-windows"
    if (Test-Path -LiteralPath $destination) {
        throw "RELEASE_ALREADY_EXISTS"
    }
    if ($PlanOnly) {
        Write-Output "PLAN_OK version=$version head=$head output=$destination"
        exit 0
    }

    $stageRoot = Join-Path $root "build\release-staging\$version"
    $workRoot = Join-Path $root "build\jev-chat-windows"
    if (Test-Path -LiteralPath $stageRoot) {
        Remove-Item -LiteralPath $stageRoot -Recurse -Force
    }
    if (Test-Path -LiteralPath $workRoot) {
        Remove-Item -LiteralPath $workRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null

    Push-Location $root
    try {
        & $python -m PyInstaller --noconfirm --clean --distpath $stageRoot --workpath $workRoot (Join-Path $root "jev.spec")
        if ($LASTEXITCODE -ne 0) { throw "PYINSTALLER_FAILED" }
    }
    finally {
        Pop-Location
    }
    $package = Join-Path $stageRoot "jev-chat-windows"
    $executable = Join-Path $package "jev-chat-windows.exe"
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw "PACKAGED_EXECUTABLE_MISSING"
    }

    $files = @()
    foreach ($file in Get-ChildItem -LiteralPath $package -Recurse -File -Force | Sort-Object FullName) {
        $relative = $file.FullName.Substring($package.Length).TrimStart('\').Replace('\', '/')
        $files += [ordered]@{
            path = $relative
            size = [int64]$file.Length
            sha256 = Get-JevSha256 $file.FullName
        }
    }
    $manifest = [ordered]@{
        schema_version = 1
        version = $version
        source_head = $head
        python_version = $pythonVersion
        requirements_lock_sha256 = Get-JevSha256 $runtimeLock
        requirements_build_lock_sha256 = Get-JevSha256 $buildLock
        files = $files
    }
    $manifestPath = Join-Path $package "release-manifest.json"
    $manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
    Assert-JevManifest $package $version | Out-Null

    $smoke = Start-Process -FilePath $executable -ArgumentList "--smoke-test" -Wait -PassThru
    if ($smoke.ExitCode -ne 0) { throw "PACKAGE_SMOKE_FAILED" }

    New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null
    Move-Item -LiteralPath $package -Destination $destination
    Write-Output "BUILD_OK version=$version head=$head manifest_sha256=$(Get-JevSha256 (Join-Path $destination 'release-manifest.json'))"
}
catch {
    # Write-Error wraps identifiers to the host width and can split error codes.
    # Keep diagnostics machine-readable while retaining a failing process exit.
    [Console]::Error.WriteLine("BUILD_FAILED: " + $_.Exception.Message)
    exit 1
}
