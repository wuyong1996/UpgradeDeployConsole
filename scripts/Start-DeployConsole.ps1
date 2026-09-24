#requires -Version 7.4
[CmdletBinding()]
param([switch]$Stop, [switch]$Status, [ValidateRange(1024,65535)][int]$Port = 5088)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$runtime = Join-Path $projectRoot '.local/deploy-console'
$apiDirectory = Join-Path $projectRoot 'src/DeployConsole.Api'
$dll = Join-Path $apiDirectory 'bin/Debug/net10.0/DeployConsole.Api.dll'
$statePath = Join-Path $runtime 'process.json'
[void][IO.Directory]::CreateDirectory($runtime)
$lock = [IO.File]::Open((Join-Path $runtime 'start.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
try {
    $owned = $null
    if (Test-Path -LiteralPath $statePath) {
        $saved = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        $candidate = Get-Process -Id $saved.processId -ErrorAction SilentlyContinue
        if ($candidate -and $candidate.StartTime.ToUniversalTime().Ticks -eq $saved.startedTicks -and $candidate.ProcessName -eq 'dotnet') { $owned = $candidate }
    }
    if ($Status) { if ($owned) { "Deploy console running: PID $($owned.Id), http://127.0.0.1:$($saved.port)" } else { 'Deploy console stopped.' }; return }
    if ($owned) { Stop-Process -Id $owned.Id; $owned.WaitForExit(10000) | Out-Null }
    if ($Stop) { if (Test-Path -LiteralPath $statePath) { Remove-Item -LiteralPath $statePath }; 'Deploy console stopped.'; return }
    if (!(Test-Path -LiteralPath $dll) -or !(Test-Path -LiteralPath (Join-Path $apiDirectory 'wwwroot/index.html'))) { throw 'Build the API and operations frontend before starting.' }
    if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) { throw "Port $Port is occupied." }
    $passwordFile = Join-Path $runtime 'password.txt'
    if (!(Test-Path -LiteralPath $passwordFile)) { [IO.File]::WriteAllText($passwordFile, "admin`n", [Text.UTF8Encoding]::new($false)) }
    $dataDirectory = Join-Path $runtime 'data'
    $targetDirectory = Join-Path $runtime 'targets'
    [void][IO.Directory]::CreateDirectory($targetDirectory)
    $arguments = @('"' + $dll + '"', '--urls', "http://127.0.0.1:$Port", '--Console:AllowLoopbackHttp=true', '--Console:DataDirectory="' + $dataDirectory + '"', '--Console:PasswordFile="' + $passwordFile + '"', '--Console:TargetsDirectory="' + $targetDirectory + '"')
    $process = Start-Process -FilePath (Get-Command dotnet).Source -ArgumentList $arguments -WorkingDirectory $apiDirectory -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtime 'stdout.log') -RedirectStandardError (Join-Path $runtime 'stderr.log') -PassThru
    @{ processId = $process.Id; startedTicks = $process.StartTime.ToUniversalTime().Ticks; port = $Port; dll = $dll } | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if ($process.HasExited) { throw 'Deploy console exited. Check .local/deploy-console/stderr.log.' }
        try { $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health/live" -TimeoutSec 2; if ($health.status -eq 'healthy') { "Deploy console ready: http://127.0.0.1:$Port"; return } } catch { }
        Start-Sleep -Milliseconds 500
    }
    throw 'Deploy console did not become ready within the startup timeout.'
} finally { $lock.Dispose() }
