param(
    [ValidateSet('Start','Run','Stop','Status')][string]$Action='Status',
    [string]$Source=(Join-Path $env:USERPROFILE 'Downloads\ctg-studies.json'),
    [string]$SnapshotDate='2026-11-19',
    [double]$DutyCycle=0.5
)
$ErrorActionPreference='Stop'
$repoRoot=Split-Path $PSScriptRoot -Parent
$runDirectory=Join-Path $repoRoot ('data\current\ctgov-download-'+$SnapshotDate.Replace('-','')+'-medcpt')
$python=Join-Path $repoRoot '.venv-hybrid\Scripts\python.exe'
New-Item -ItemType Directory -Path $runDirectory -Force | Out-Null
if ($Action -eq 'Status') {
    $progress=Join-Path $runDirectory 'progress.json'
    if (Test-Path -LiteralPath $progress) { Get-Content -LiteralPath $progress }
    else { Write-Host 'No progress file yet.' }
    return
}
if ($Action -eq 'Stop') {
    New-Item -ItemType File -Path (Join-Path $runDirectory 'stop.request') -Force | Out-Null
    Write-Host 'Graceful stop requested. The current inference batch will be committed before exit.'
    return
}
if (-not (Test-Path -LiteralPath $Source)) { throw "Downloaded JSON not found: $Source" }
$env:PYTHONDONTWRITEBYTECODE='1'
if ($Action -eq 'Run') {
    Push-Location -LiteralPath $repoRoot
    try { & $python -u -m src.current_medcpt --source $Source --run-dir $runDirectory --snapshot-date $SnapshotDate --duty-cycle $DutyCycle --clear-stop }
    finally { Pop-Location }
} else {
    # Start-Process joins its argument array into a Windows command line, so
    # quote filesystem paths explicitly. No shell is launched by the worker.
    $workerArgs=@('-u','-m','src.current_medcpt','--source',('"'+$Source+'"'),'--run-dir',('"'+$runDirectory+'"'),
        '--snapshot-date',$SnapshotDate,'--duty-cycle',$DutyCycle.ToString([Globalization.CultureInfo]::InvariantCulture),'--clear-stop')
    $stamp=Get-Date -Format 'yyyyMMdd-HHmmss-ffff'
    $worker=Start-Process -FilePath $python -ArgumentList $workerArgs -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $runDirectory ($stamp+'.stdout.log')) `
        -RedirectStandardError (Join-Path $runDirectory ($stamp+'.stderr.log'))
    Write-Host "Started worker PID $($worker.Id). Progress: $runDirectory\progress.json"
}
