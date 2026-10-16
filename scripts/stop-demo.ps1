$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$stateFile = Join-Path $projectRoot '.runtime/processes.json'
if (!(Test-Path -LiteralPath $stateFile)) { Write-Host 'No managed demo processes.'; exit }
foreach ($item in (Get-Content -Raw $stateFile | ConvertFrom-Json)) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($item.Id)"
    if ($process -and $process.CreationDate.ToUniversalTime().Ticks.ToString() -eq $item.StartedTicks -and $process.CommandLine -eq $item.CommandLine) {
        & taskkill.exe /PID $item.Id /T /F | Out-Null
        Write-Host "Stopped $($item.Name)"
    } elseif ($process) {
        throw "Process identity changed for $($item.Name); preserving state file for inspection."
    }
}
Remove-Item -LiteralPath $stateFile
Write-Host 'Infrastructure and persisted data remain available in Docker.'
