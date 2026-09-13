<# Start a durable native Windows queue through the Windows process service.
The worker runs independently of the launching shell. Windows must remain
awake and signed in, with access to the ANSYS license server.
#>
param(
    [Parameter(Mandatory = $true)][string]$Python,
    [string]$Work,
    [string]$Examples,
    [string]$Libraries,
    [string]$ReusePassed,
    [ValidateRange(1, 8)][int]$RenderScale = 4
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
if (-not $Work) { $Work = Join-Path $repoRoot ('outputs\queue_' + (Get-Date -Format 'yyyyMMdd_HHmmss')) }
if (-not $Examples) { $Examples = Join-Path $repoRoot 'docs\examples' }
$Python = (Resolve-Path $Python).Path
$Work = [IO.Path]::GetFullPath($Work)
$Examples = [IO.Path]::GetFullPath($Examples)
if (Test-Path $Work) { throw 'Choose a new work directory; use the saved resume command for an existing queue.' }
if ([IO.Path]::GetPathRoot($Work) -ne [IO.Path]::GetPathRoot($Examples)) {
    throw 'Work and example directories must share a volume for verified folder replacement.'
}
New-Item -ItemType Directory -Path $Work | Out-Null
$snapshot = Join-Path $Work 'source'
New-Item -ItemType Directory -Path $snapshot | Out-Null
foreach ($folder in @('src', 'scripts', 'configs', 'assets')) {
    Copy-Item -LiteralPath (Join-Path $repoRoot $folder) -Destination $snapshot -Recurse
}
if ($Libraries) {
    Copy-Item -LiteralPath (Resolve-Path $Libraries).Path -Destination (Join-Path $snapshot 'native') -Recurse
}
# Resolve external and relative mesh assets now, before the unattended queue starts.
& $Python -B (Join-Path $snapshot 'scripts\freeze_mesh_inputs.py') --source $repoRoot --snapshot $snapshot
if ($LASTEXITCODE -ne 0) { throw 'Failed to freeze imported mesh inputs; queue was not started.' }
# Record only portable relative names and hashes in the code snapshot manifest.
$hashes = @{}
Get-ChildItem $snapshot -Recurse -File | Where-Object { $_.FullName -notmatch '__pycache__' } | ForEach-Object {
    $relative = $_.FullName.Substring($snapshot.Length + 1).Replace('\', '/')
    $hashes[$relative] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower()
}
$hashes | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $Work 'snapshot-manifest.json') -Encoding UTF8
$settings = @{ python=$Python; work=$Work; examples=$Examples; scale=$RenderScale; snapshot=$snapshot; libraries=$(if ($Libraries) { Join-Path $snapshot "native" } else { $null }); reuse_passed=$(if ($ReusePassed) { (Resolve-Path $ReusePassed).Path } else { $null }) }
$settings | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Work 'launcher.json') -Encoding UTF8
$bootstrap = @'
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent
settings = json.loads((root / 'launcher.json').read_text(encoding='utf-8-sig'))
command = [settings['python'], '-B', '-u',
           str(Path(settings['snapshot']) / 'scripts/render_queue.py'),
           '--work', settings['work'], '--examples', settings['examples'],
           '--render-scale', str(settings['scale'])]
if settings.get('libraries'):
    command.extend(['--libraries', settings['libraries']])
if settings.get('reuse_passed'):
    command.extend(['--reuse-passed', settings['reuse_passed']])
with (root / 'worker.log').open('ab', buffering=0) as log:
    result = subprocess.run(command, cwd=settings['snapshot'], stdin=subprocess.DEVNULL,
                            stdout=log, stderr=subprocess.STDOUT,
                            creationflags=subprocess.CREATE_NO_WINDOW)
sys.exit(result.returncode)
'@
$bootstrapPath = Join-Path $Work 'bootstrap.py'
$bootstrap | Set-Content -LiteralPath $bootstrapPath -Encoding UTF8
# WMI creates an independent process, outside the launching shell's process tree.
$commandLine = '"' + $Python + '" -B "' + $bootstrapPath + '"'
$result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine=$commandLine; CurrentDirectory=$Work }
if ($result.ReturnValue -ne 0) { throw ('Windows process service rejected launch: ' + $result.ReturnValue) }
@{ bootstrap_pid=$result.ProcessId; started_utc=(Get-Date).ToUniversalTime().ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Work 'launch-process.json') -Encoding UTF8
$resume = '"' + $Python + '" -B "' + (Join-Path $snapshot 'scripts\render_queue.py') + '" --work "' + $Work + '" --examples "' + $Examples + '" --render-scale ' + $RenderScale + ' --retry-failed'
if ($Libraries) { $resume += ' --libraries "' + (Join-Path $snapshot 'native') + '"' }
$resume | Set-Content -LiteralPath (Join-Path $Work 'resume-command.txt') -Encoding UTF8
Write-Output ('Detached queue bootstrap PID: ' + $result.ProcessId)
Write-Output ('Progress: ' + (Join-Path $Examples 'queue-status.json'))
Write-Output ('Private logs and restart command: ' + $Work)
