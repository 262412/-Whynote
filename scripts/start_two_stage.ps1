param(
    [ValidateSet('Preview', 'Mock', 'Live', 'Report', 'Derive', 'Materials')][string]$Mode = 'Preview',
    [string]$BatchId = 'offline-v1',
    [string]$DataRoot = '',
    [string]$Python = '',
    [string]$ConfigFile = '',
    [string]$KeysFile = '',
    [string]$SourceBatch = '',
    [string]$OutputDir = '',
    [ValidateSet('Mock', 'Live')][string]$ReportMode = 'Mock'
)
$ErrorActionPreference = 'Stop'
try {
    $projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    if (-not $DataRoot) { $DataRoot = $projectRoot }
    $DataRoot = [IO.Path]::GetFullPath($DataRoot)
    if (-not $Python) { $Python = Join-Path $DataRoot '.venv/Scripts/python.exe' }
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        Write-Output '{"status":"BLOCKED","code":"python_missing_supply_Python_or_install_locked_dev_environment"}'
        exit 2
    }
    # -I ignores PYTHONPATH and user site. Insert only this script's checkout explicitly.
    # Single-quoted Python literals also survive Windows PowerShell native argument passing.
    $bootstrap = @'
import pathlib, sys
root = pathlib.Path(sys.argv.pop(1)).resolve()
required = ('pyproject.toml', 'src/whynote/__init__.py', 'src/whynote/two_stage.py', 'src/whynote/two_stage_batch.py')
if not all((root / item).is_file() for item in required):
    print('{\"status\":\"BLOCKED\",\"code\":\"checkout_missing_modules_use_complete_checkout\"}')
    sys.exit(2)
sys.path.insert(0, str(root / 'src'))
try:
    from whynote.two_stage_batch import main
except (ImportError, SyntaxError):
    print('{\"status\":\"BLOCKED\",\"code\":\"source_import_failed_check_checkout_and_locked_environment\"}')
    sys.exit(2)
sys.exit(main(project_root=root))
'@
    $arguments = @('-I', '-B', '-X', 'utf8', '-c', $bootstrap, $projectRoot,
        '--mode', $Mode.ToLowerInvariant(), '--data-root', $DataRoot,
        '--batch-id', $BatchId, '--report-mode', $ReportMode.ToLowerInvariant())
    if ($ConfigFile) { $arguments += @('--config-file', [IO.Path]::GetFullPath($ConfigFile)) }
    if ($KeysFile) { $arguments += @('--keys-file', [IO.Path]::GetFullPath($KeysFile)) }
    if ($SourceBatch) { $arguments += @('--source-batch', [IO.Path]::GetFullPath($SourceBatch)) }
    if ($OutputDir) { $arguments += @('--output-dir', [IO.Path]::GetFullPath($OutputDir)) }
    & $Python @arguments
    exit $LASTEXITCODE
} catch {
    Write-Output '{"status":"BLOCKED","code":"launcher_failed_check_explicit_paths_and_locked_environment"}'
    exit 2
}
