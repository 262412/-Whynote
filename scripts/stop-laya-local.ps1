param([Parameter(Mandatory = $true)][string]$ProjectRoot, [int]$Port = 8766)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $ProjectRoot).Path
$receipt = Get-Content -LiteralPath (Join-Path $root "var/laya/service-$Port.json") -Raw | ConvertFrom-Json
$process = Get-CimInstance Win32_Process -Filter "ProcessId = $($receipt.process_id)"
if (!$process) { Write-Output 'The recorded Laya process has already stopped.'; return }
if ($process.ExecutablePath -ne $receipt.python -or
    !$process.CommandLine.Contains('-m whynote.laya_app ') -or
    !$process.CommandLine.Contains($receipt.model_dir) -or
    !$process.CommandLine.Contains("--port $Port ")) {
    throw 'Process identity changed; refusing to stop a different process.'
}
# Windows venv python.exe may be a launcher with a separate Python worker.
# During startup the receipt still points to that launcher. Stop only its
# directly related worker with the exact module and model arguments.
Get-CimInstance Win32_Process -Filter "ParentProcessId = $($receipt.process_id)" | Where-Object {
    $_.CommandLine -and $_.CommandLine.Contains('-m whynote.laya_app ') -and
    $_.CommandLine.Contains($receipt.model_dir) -and $_.CommandLine.Contains("--port $Port ")
} | ForEach-Object { Stop-Process -Id $_.ProcessId }
if (Get-Process -Id $receipt.process_id -ErrorAction SilentlyContinue) {
    Stop-Process -Id $receipt.process_id
}
Write-Output 'Local Laya stopped. Model files and test artifacts are preserved.'
