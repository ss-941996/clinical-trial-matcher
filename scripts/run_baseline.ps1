param([switch]$SkipDownload)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.uv-cache'
$env:UV_PYTHON_INSTALL_DIR = Join-Path (Get-Location) '.python'
uv sync --frozen
if ($LASTEXITCODE -ne 0) { throw 'Environment setup failed' }
$python = Join-Path (Get-Location) '.venv\Scripts\python.exe'
& $python -m src.diagnostics
if ($LASTEXITCODE -ne 0) { throw 'Diagnostics failed' }
& $python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
if (-not $SkipDownload) {
    & $python -u -m src.download
    if ($LASTEXITCODE -ne 0) { throw 'Download failed. See README for manual archive fallback.' }
}
& $python -u -m src.clean
if ($LASTEXITCODE -ne 0) { throw 'Cleaning failed' }
& $python -u -m src.retrieve build
if ($LASTEXITCODE -ne 0) { throw 'Indexing failed' }
& $python -u -m src.evaluate
if ($LASTEXITCODE -ne 0) { throw 'Evaluation failed' }
