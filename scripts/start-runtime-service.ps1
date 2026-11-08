param(
    [string]$Python = (Join-Path $PSScriptRoot '../../.venvs/runtime-service/Scripts/python.exe')
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
if (Get-NetTCPConnection -State Listen -LocalPort 8010 -ErrorAction SilentlyContinue) {
    throw 'Port 8010 is already in use.'
}
Push-Location $projectRoot
try {
    $env:PYTHONUTF8='1'
    & $pythonPath scripts/service_init.py
    if ($LASTEXITCODE -ne 0) { throw 'Database initialization failed.' }
    $logPath = Join-Path $projectRoot 'artifacts/service/logs'
    New-Item -ItemType Directory -Force -Path $logPath | Out-Null
    $api = Start-Process -FilePath $pythonPath -ArgumentList @('-X','utf8','-m','pico.service.api') -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logPath 'api.log') -RedirectStandardError (Join-Path $logPath 'api.err.log')
    $workers = foreach ($number in 1,2) {
        Start-Process -FilePath $pythonPath -ArgumentList @('-X','utf8','-m','pico.service.worker','--worker-id',"local-$number") -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logPath "worker-$number.log") -RedirectStandardError (Join-Path $logPath "worker-$number.err.log")
    }
    @{api_launcher=$api.Id;worker_launchers=@($workers.Id)} | ConvertTo-Json | Set-Content (Join-Path $logPath 'launchers.json')
    Write-Output 'Starting API and two workers: http://127.0.0.1:8010/'
} finally {
    Pop-Location
}
