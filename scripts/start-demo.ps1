param([switch]$SkipInstall, [switch]$Restart)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $projectRoot 'backend'
$frontendRoot = Join-Path $projectRoot 'frontend'
$runtimeRoot = Join-Path $projectRoot '.runtime'
New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
Set-Location $projectRoot
if (!(Test-Path '.env')) { throw 'Create .env from .env.example and set credentials first.' }
$envText = Get-Content -Raw '.env'
if ($envText -notmatch '(?m)^APP_TOKEN=.{16,}') { throw 'Set APP_TOKEN (at least 16 characters) in .env.' }
$accessKey = [regex]::Match($envText, '(?m)^APP_TOKEN=(.+)$').Groups[1].Value.Trim().Trim('"').Trim("'")
$stateFile = Join-Path $runtimeRoot 'processes.json'
$activeProcesses = @{}
foreach ($observedProcess in (Get-CimInstance Win32_Process)) {
    $activeProcesses[[int]$observedProcess.ProcessId] = $observedProcess
}
$managed = @()
if (Test-Path -LiteralPath $stateFile) {
    $managed = Get-Content -Raw -LiteralPath $stateFile | ConvertFrom-Json
}

function Test-ManagedIdentity($item) {
    $observed = $activeProcesses[[int]$item.Id]
    return $observed -and $observed.CreationDate.ToUniversalTime().Ticks.ToString() -eq $item.StartedTicks -and $observed.CommandLine -eq $item.CommandLine
}

function Test-ManagedListener($port, $serviceName) {
    $root = @($managed | Where-Object { $_.Name -eq $serviceName })
    if ($root.Count -ne 1 -or !(Test-ManagedIdentity $root[0])) { return $false }
    $listeners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
    if ($listeners.Count -eq 0) { return $false }
    foreach ($listener in $listeners) {
        $currentId = [int]$listener.OwningProcess
        $visited = @{}
        while ($currentId -ne [int]$root[0].Id) {
            if ($visited.ContainsKey($currentId) -or !$activeProcesses.ContainsKey($currentId)) { return $false }
            $visited[$currentId] = $true
            $currentId = [int]$activeProcesses[$currentId].ParentProcessId
        }
    }
    return $true
}

if ($Restart -and $managed.Count -gt 0) {
    foreach ($item in $managed) {
        if ($activeProcesses.ContainsKey([int]$item.Id) -and !(Test-ManagedIdentity $item)) {
            throw "Cannot restart: process identity changed for $($item.Name). Inspect .runtime/processes.json."
        }
    }
    & (Join-Path $PSScriptRoot 'stop-demo.ps1')
} elseif (!$Restart) {
    $allManagedAlive = $managed.Count -eq 4 -and @($managed | Where-Object { !(Test-ManagedIdentity $_) }).Count -eq 0
    if ($allManagedAlive -and (Test-ManagedListener 8000 'api') -and (Test-ManagedListener 3000 'web')) {
        try {
            $health = Invoke-RestMethod 'http://127.0.0.1:8000/health' -TimeoutSec 3
            $snapshot = Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/dashboard' -Headers @{ Authorization="Bearer $accessKey" } -TimeoutSec 3
            $web = Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:3000' -TimeoutSec 3
            if ($health.status -eq 'ok' -and $snapshot.source -eq 'simulation' -and $web.StatusCode -eq 200) {
                Write-Host 'Demo is already running. Reusing the existing session.'
                Write-Host 'Dashboard: http://localhost:3000 | API docs: http://localhost:8000/docs'
                Write-Host 'Sign in with APP_TOKEN from .env. Use -Restart to rebuild/restart after code changes.'
                exit 0
            }
        } catch { }
    }
}
foreach ($port in @(8000, 3000)) {
    $listeners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
    if ($listeners.Count -gt 0) {
        $ownerIds = ($listeners | Select-Object -ExpandProperty OwningProcess -Unique) -join ', '
        throw "Port $port is in use by PID(s) $ownerIds. For a managed demo, rerun with -Restart; otherwise inspect that process before stopping it."
    }
}
$dockerReady = $false
try {
    docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
    $dockerReady = $LASTEXITCODE -eq 0
} catch { }
if (!$dockerReady) {
    docker desktop start
    if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop with Linux containers and retry.' }
}
docker compose --profile local-mqtt up -d --wait --wait-timeout 120 postgres minio mqtt
if ($LASTEXITCODE -ne 0) { throw 'Infrastructure startup failed. Build MinIO first if needed.' }
docker compose run --rm minio-init
if ($LASTEXITCODE -ne 0) { throw 'MinIO initialization failed.' }
Set-Location $backendRoot
if (!$SkipInstall) {
    uv sync --locked
    if ($LASTEXITCODE -ne 0) { throw 'uv sync failed' }
}
uv run --locked alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Migration failed' }
Set-Location $frontendRoot
if (!$SkipInstall) {
    npm ci
    if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
}
$pythonExe = Join-Path $backendRoot '.venv/Scripts/python.exe'
$nodeExe = (Get-Command node).Source
$processes = @()
try {
    foreach ($service in @(
        @{ Name='api'; Exe=$pythonExe; Dir=$backendRoot; Args=@('-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000') },
        @{ Name='worker'; Exe=$pythonExe; Dir=$backendRoot; Args=@('-m','app.workers.inference') },
        @{ Name='simulator'; Exe=$pythonExe; Dir=$backendRoot; Args=@('-m','app.workers.simulator') },
        @{ Name='web'; Exe=$nodeExe; Dir=$frontendRoot; Args=@('node_modules/next/dist/bin/next','start','--hostname','127.0.0.1','--port','3000') }
    )) {
        $process = Start-Process -FilePath $service.Exe -ArgumentList $service.Args -WorkingDirectory $service.Dir -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeRoot "$($service.Name).out.log") -RedirectStandardError (Join-Path $runtimeRoot "$($service.Name).err.log")
        $observed = Get-CimInstance Win32_Process -Filter "ProcessId = $($process.Id)"
        if (!$observed) { throw "Process $($service.Name) exited during startup." }
        $processes += @{ Name=$service.Name; Id=$process.Id; StartedTicks=$observed.CreationDate.ToUniversalTime().Ticks.ToString(); CommandLine=$observed.CommandLine }
    }
    $processes | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $runtimeRoot 'processes.json')
    $healthy = $false
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Seconds 1
        try {
            $health = Invoke-RestMethod 'http://127.0.0.1:8000/health' -TimeoutSec 3
            $snapshot = Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/dashboard' -Headers @{ Authorization="Bearer $accessKey" } -TimeoutSec 3
            $web = Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:3000' -TimeoutSec 2
            if ($health.status -eq 'ok' -and $health.mqtt_connected -and !$snapshot.stale -and $snapshot.device.online -and $web.StatusCode -eq 200) { $healthy=$true; break }
        } catch { }
    }
    if (!$healthy) { throw 'Demo startup timed out. Inspect .runtime logs.' }
    foreach ($item in $processes) {
        if (!(Get-CimInstance Win32_Process -Filter "ProcessId = $($item.Id)")) {
            throw "Service $($item.Name) exited. Inspect its .runtime log."
        }
    }
    Write-Host 'Dashboard: http://localhost:3000 | API docs: http://localhost:8000/docs'
    Write-Host 'Sign in with APP_TOKEN from .env. Logs: .runtime/'
} catch {
    foreach ($item in $processes) { & taskkill.exe /PID $item.Id /T /F 2>$null | Out-Null }
    throw
} finally { Set-Location $projectRoot }
