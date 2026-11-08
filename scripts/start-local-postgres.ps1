param(
    [string]$PgRoot = (Join-Path $PSScriptRoot '../../.tools/postgresql-17.6')
)
$ErrorActionPreference = 'Stop'
$resolvedRoot = (Resolve-Path -LiteralPath $PgRoot).Path
$dataPath = Join-Path $resolvedRoot 'data'
$control = Join-Path $resolvedRoot 'pgsql/bin/pg_ctl.exe'
if (-not (Test-Path (Join-Path $dataPath 'PG_VERSION'))) {
    throw 'This helper only starts an existing initialized local cluster.'
}
& $control -D $dataPath status
if ($LASTEXITCODE -ne 0) {
    & $control -D $dataPath -l (Join-Path $resolvedRoot 'server.log') -o '-p 5438 -h 127.0.0.1' -w start
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL failed to start.' }
}
