param(
    [ValidateSet('Prepare','Serve','Import','Sample-Size','Search','Freeze','Confirm','Supplement','Preview','Mock','Live','Report')][string]$Mode = 'Prepare',
    [string]$DataRoot = '',
    [string]$Python = '',
    [string]$Workflow = '',
    [string]$SourceBatch = '',
    [string]$CompareBatch = '',
    [string]$GroupsFile = '',
    [string]$OutputDir = '',
    [string]$LabelsFile = '',
    [string]$PredictionDir = '',
    [string]$SearchFile = '',
    [string]$SupplementFile = '',
    [string]$TargetId = '',
    [string]$ConfigFile = '',
    [string]$KeysFile = '',
    [string]$BatchId = '',
    [string]$Seed = 'jev-review-20261006-v1',
    [int]$CalibrationCount = 400,
    [int]$Port = 0
)
$ErrorActionPreference = 'Stop'
try {
    $projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    if (-not $DataRoot) { $DataRoot = $projectRoot }
    if (-not $Python) { $Python = Join-Path $projectRoot '.venv/Scripts/python.exe' }
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'python_missing' }
    $bootstrap = @'
import pathlib, sys
root = pathlib.Path(sys.argv.pop(1)).resolve()
sys.path.insert(0, str(root / 'src'))
from whynote.two_stage_research import main
sys.exit(main(project_root=root))
'@
    $arguments = @('-I','-B','-X','utf8','-c',$bootstrap,$projectRoot,
        '--mode',$Mode.ToLowerInvariant(),'--data-root',[IO.Path]::GetFullPath($DataRoot),
        '--seed',$Seed,'--calibration-count',$CalibrationCount,'--port',$Port)
    $paths = @{
        'workflow'=$Workflow; 'source-batch'=$SourceBatch; 'compare-batch'=$CompareBatch;
        'groups-file'=$GroupsFile; 'output-dir'=$OutputDir; 'labels-file'=$LabelsFile;
        'prediction-dir'=$PredictionDir; 'search-file'=$SearchFile; 'supplement-file'=$SupplementFile;
        'config-file'=$ConfigFile; 'keys-file'=$KeysFile
    }
    foreach ($name in $paths.Keys) {
        if ($paths[$name]) { $arguments += @(('--' + $name), [IO.Path]::GetFullPath($paths[$name])) }
    }
    if ($TargetId) { $arguments += @('--target-id',$TargetId) }
    if ($BatchId) { $arguments += @('--batch-id',$BatchId) }
    & $Python @arguments
    exit $LASTEXITCODE
} catch {
    Write-Output '{"status":"BLOCKED","code":"research_launcher_failed_check_explicit_paths"}'
    exit 2
}
