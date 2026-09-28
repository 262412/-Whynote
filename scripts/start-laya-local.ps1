param(
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [ValidateSet('cuda', 'cpu')][string]$Device = 'cuda',
    [ValidateRange(1024, 65535)][int]$Port = 8766
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $ProjectRoot).Path
$python = Join-Path $root 'var/laya-runtime/Scripts/python.exe'
$model = Join-Path $root 'var/models/laya/multilingual'
$runDir = Join-Path $root 'var/laya'
if (!(Test-Path -LiteralPath $python) -or !(Test-Path -LiteralPath $model)) {
    throw 'Install the local runtime and multilingual checkpoint first. See docs/laya-local.md.'
}
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $Port is already in use. Open http://127.0.0.1:$Port or stop the existing service first."
}
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$stdout = Join-Path $runDir "$stamp.stdout.log"
$stderr = Join-Path $runDir "$stamp.stderr.log"
$arguments = '-m whynote.laya_app --model-dir "{0}" --device {1} --port {2} --enable-local-test' -f $model, $Device, $Port
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
@{ process_id = $process.Id; python = $python; model_dir = $model; port = $Port } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runDir "service-$Port.json") -Encoding utf8
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    if ($process.HasExited) { throw "Laya exited. Inspect $stderr" }
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 1
        if ($health.status -eq 'ready') {
            $listener = Get-NetTCPConnection -LocalPort $Port -State Listen
            $worker = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)"
            if ($worker.ProcessId -ne $process.Id -and $worker.ParentProcessId -ne $process.Id) {
                throw 'The listening process does not belong to this launcher.'
            }
            @{ process_id = $worker.ProcessId; python = $worker.ExecutablePath; model_dir = $model; port = $Port } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runDir "service-$Port.json") -Encoding utf8
            Write-Output "Laya ready: http://127.0.0.1:$Port  device=$($health.device)  PID=$($worker.ProcessId)"
            return
        }
    } catch { }
    Start-Sleep -Seconds 1
}
throw "Startup is still pending. Inspect $stderr; use stop-laya-local.ps1 to stop this process."
