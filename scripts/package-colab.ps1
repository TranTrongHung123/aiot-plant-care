param([switch]$IncludeDatasets)
$ErrorActionPreference = 'Stop'
$packageScript = Join-Path (Split-Path $PSScriptRoot -Parent) 'ml/scripts/package_colab.py'
$arguments = @('run', '--no-project', '--python', '3.12', $packageScript)
if ($IncludeDatasets) { $arguments += '--include-datasets' }
& uv @arguments
if ($LASTEXITCODE -ne 0) { throw 'Colab archive packaging failed.' }
