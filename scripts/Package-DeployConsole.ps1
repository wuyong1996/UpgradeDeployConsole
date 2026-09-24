#requires -Version 7.4
[CmdletBinding()]
param([ValidatePattern('^[A-Za-z0-9][A-Za-z0-9-]{0,79}$')][string]$Version = [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmssfff'))

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-PackagePath {
    param([string]$Path, [string]$Boundary, [switch]$Shallow)
    $absolute = [IO.Path]::GetFullPath($Path)
    $prefix = [IO.Path]::TrimEndingDirectorySeparator([IO.Path]::GetFullPath($Boundary)) + [IO.Path]::DirectorySeparatorChar
    if (-not $absolute.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw "Package path escapes its boundary: $absolute" }
    if (Test-Path -LiteralPath $absolute) {
        $item = Get-Item -LiteralPath $absolute -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Linked package path is not allowed: $absolute" }
        if ($item.PSIsContainer -and -not $Shallow) {
            foreach ($child in Get-ChildItem -LiteralPath $absolute -Force) { Assert-PackagePath -Path $child.FullName -Boundary $Boundary }
        }
    }
}

function Get-OldPackageArtifacts {
    param([string]$Directory, [string[]]$Keep = @())
    $parent = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($Directory))
    Assert-PackagePath -Path $Directory -Boundary $parent
    if (-not (Test-Path -LiteralPath $Directory)) { return }
    foreach ($item in Get-ChildItem -LiteralPath $Directory -Force) {
        if ($item.Name -in $Keep) { continue }
        if ($item.Name -notmatch '^(deploy-console-.+|upgrade-deploy-console(?:-[A-Za-z0-9-]+)?\.sh|published|DEPLOY\.md|latest\.json)$') {
            throw "Unexpected file in deployment-artifacts; move it elsewhere before packaging: $($item.Name)"
        }
        $item
    }
}

function Clear-OldPackageArtifacts {
    param([string]$Directory, [string[]]$Keep)
    # Finish validation of the whole candidate set before the first removal.
    $old = @(Get-OldPackageArtifacts -Directory $Directory -Keep $Keep)
    foreach ($item in $old) {
        Assert-PackagePath -Path $item.FullName -Boundary $Directory
        Remove-Item -LiteralPath $item.FullName -Recurse -Force
    }
    return $old.Count
}

function Invoke-PackageCommand {
    param([string]$Program, [string[]]$CommandArguments)
    & $Program @CommandArguments
    if ($LASTEXITCODE -ne 0) { throw "Package command failed ($LASTEXITCODE): $Program" }
}

function Invoke-DeployConsolePackage {
    param([string]$ProjectRoot, [string]$PackageVersion)
    if ($PackageVersion -notmatch '^[A-Za-z0-9][A-Za-z0-9-]{0,79}$') { throw 'Invalid package version' }
    $project = (Resolve-Path -LiteralPath $ProjectRoot).Path
    if ((Get-Item -LiteralPath $project).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Project root cannot be a link' }
    $artifacts = Join-Path $project 'deployment-artifacts'
    $local = Join-Path $project '.local'
    Assert-PackagePath -Path $local -Boundary $project -Shallow
    Assert-PackagePath -Path $artifacts -Boundary $project
    [void][IO.Directory]::CreateDirectory($local)
    $lockPath = Join-Path $local 'package.lock'
    Assert-PackagePath -Path $lockPath -Boundary $local
    $lock = [IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None')
    $stage = Join-Path $local ('package-' + [Guid]::NewGuid().ToString('N'))
    $archiveName = "deploy-console-server-$PackageVersion.tar.gz"
    $outputNames = @($archiveName, 'upgrade-deploy-console.sh', 'DEPLOY.md')
    try {
        [void]@(Get-OldPackageArtifacts -Directory $artifacts)
        if (Test-Path -LiteralPath (Join-Path $artifacts $archiveName)) { throw 'This version already exists; use a new version' }
        [void][IO.Directory]::CreateDirectory($stage)
        $payload = Join-Path $stage "deploy-console-upload-$PackageVersion"
        $published = Join-Path $payload 'published'
        [void][IO.Directory]::CreateDirectory($published)
        Push-Location -LiteralPath $project
        try {
            Invoke-PackageCommand -Program 'npm' -CommandArguments @('ci', '--prefix', 'src/frontend')
            Invoke-PackageCommand -Program 'npm' -CommandArguments @('run', 'build', '--prefix', 'src/frontend')
            Invoke-PackageCommand -Program 'dotnet' -CommandArguments @('restore', 'src/DeployConsole.Api/DeployConsole.Api.csproj', '--locked-mode')
            Invoke-PackageCommand -Program 'dotnet' -CommandArguments @('publish', 'src/DeployConsole.Api/DeployConsole.Api.csproj', '-c', 'Release', '-p:UseAppHost=false', '--no-restore', '-o', $published, '--verbosity', 'minimal')
            Invoke-PackageCommand -Program 'python' -CommandArguments @('scripts/package_deploy_console.py', '--project', $project, '--stage', $stage, '--version', $PackageVersion)
        } finally { Pop-Location }
        $delivery = Join-Path $stage 'delivery'
        Assert-PackagePath -Path $delivery -Boundary $stage
        foreach ($name in $outputNames) {
            if (-not (Test-Path -LiteralPath (Join-Path $delivery $name) -PathType Leaf)) { throw "Missing validated output: $name" }
        }
        # A build or verification failure cannot delete the previously delivered package.
        [void]@(Get-OldPackageArtifacts -Directory $artifacts)
        [void][IO.Directory]::CreateDirectory($artifacts)
        foreach ($name in $outputNames) {
            $from = Join-Path $delivery $name
            $to = Join-Path $artifacts $name
            Assert-PackagePath -Path $to -Boundary $artifacts
            Copy-Item -LiteralPath $from -Destination $to -Force
            if ((Get-FileHash -LiteralPath $from -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $to -Algorithm SHA256).Hash) { throw "Output copy verification failed: $name" }
        }
        $removed = Clear-OldPackageArtifacts -Directory $artifacts -Keep $outputNames
        "Package ready: $(Join-Path $artifacts $archiveName)"
        "Removed $removed old artifact entries; only the latest archive, upgrade script and DEPLOY.md remain."
    } finally {
        try {
            if (Test-Path -LiteralPath $stage) {
                Assert-PackagePath -Path $stage -Boundary $local
                Remove-Item -LiteralPath $stage -Recurse -Force
            }
        } finally { $lock.Dispose() }
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    Invoke-DeployConsolePackage -ProjectRoot ([IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))) -PackageVersion $Version
}
