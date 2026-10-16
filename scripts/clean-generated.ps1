[CmdletBinding(SupportsShouldProcess)]
param([switch]$Apply, [switch]$RemoveLocalMlEnvironments)

$ErrorActionPreference = 'Stop'
$workspaceRoot = (Resolve-Path -LiteralPath (Split-Path $PSScriptRoot -Parent)).Path
$targets = [System.Collections.Generic.List[string]]::new()
$skipNames = @('.git', '.venv', 'node_modules', 'data', 'outputs', 'checkpoints', 'firmware', '.next')
$cacheNames = @('__pycache__', '.pytest_cache', '.ruff_cache', '.ipynb_checkpoints')

function Find-Cache([string]$directory) {
    foreach ($item in Get-ChildItem -LiteralPath $directory -Force -Directory) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
        if ($item.Name -in $skipNames) { continue }
        if ($item.Name -in $cacheNames) { $targets.Add($item.FullName) }
        else { Find-Cache $item.FullName }
    }
}

Find-Cache $workspaceRoot
foreach ($relative in @(
    'frontend/.next', 'frontend/test-results', 'frontend/playwright-report',
    'frontend/tsconfig.tsbuildinfo'
)) {
    $candidate = Join-Path $workspaceRoot $relative
    if (Test-Path -LiteralPath $candidate) { $targets.Add($candidate) }
}
if ($RemoveLocalMlEnvironments) {
    foreach ($relative in @('ml/.venv', 'ml/leaf-disease/.venv', 'ml/fruit-health/.venv', 'ml/weather/.venv')) {
        $candidate = Join-Path $workspaceRoot $relative
        if (Test-Path -LiteralPath $candidate) { $targets.Add($candidate) }
    }
}
$runtimePath = Join-Path $workspaceRoot '.runtime'
if (Test-Path -LiteralPath $runtimePath) {
    Get-ChildItem -LiteralPath $runtimePath -File -Filter '*.log' |
        ForEach-Object { $targets.Add($_.FullName) }
}

# Validate the complete plan before deleting anything. Never follow junctions.
$plan = foreach ($candidate in $targets) {
    $resolved = (Resolve-Path -LiteralPath $candidate).Path
    if (!$resolved.StartsWith($workspaceRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Outside workspace: $resolved"
    }
    $ancestor = Get-Item -LiteralPath $resolved -Force
    while ($ancestor.FullName -ne $workspaceRoot) {
        if ($ancestor.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Refuse reparse point: $($ancestor.FullName)"
        }
        $ancestor = Get-Item -LiteralPath (Split-Path $ancestor.FullName -Parent) -Force
    }
    $item = Get-Item -LiteralPath $resolved -Force
    if ($item.PSIsContainer -and $item.Name -eq 'dist') {
        $allowedArchive = switch ($item.Parent.Name) {
            'leaf-disease' { 'leaf-training-src.zip' }
            'fruit-health' { 'fruit-training-src.zip' }
            'weather' { 'weather-training-src.zip' }
        }
        $unexpected = @(Get-ChildItem -LiteralPath $resolved -Force |
            Where-Object { $_.PSIsContainer -or $_.Name -ne $allowedArchive })
        if ($unexpected.Count) { throw "Keep dist containing other files (possibly data): $resolved" }
    }
    $size = $item.Length
    if ($item.PSIsContainer) {
        $children = @(Get-ChildItem -LiteralPath $resolved -Recurse -Force)
        if ($children | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }) {
            throw "Refuse nested reparse point: $resolved"
        }
        $size = ($children | Where-Object { !$_.PSIsContainer } | Measure-Object Length -Sum).Sum
    }
    [PSCustomObject]@{ Path = $resolved; MiB = [math]::Round($size / 1MB, 2) }
}
$plan | Format-Table -AutoSize
if (!$Apply) {
    Write-Host 'Preview only. Stop the demo and notebook kernels, then rerun with -Apply.'
    return
}

# Training now runs in Colab; cleanup does not require a local CUDA installation.
$active = @(Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -and $_.ExecutablePath.StartsWith($workspaceRoot + '\', [StringComparison]::OrdinalIgnoreCase)
})
if ($active.Count) { throw 'Stop Python/notebook/demo processes in this workspace before cleanup.' }
foreach ($entry in $plan) {
    if ($PSCmdlet.ShouldProcess($entry.Path, 'Delete generated files')) {
        Remove-Item -LiteralPath $entry.Path -Recurse -Force
    }
}
