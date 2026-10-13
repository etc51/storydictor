param(
    [ValidateSet('init','run','status','account-check','finish')][string]$Action = 'status',
    [Parameter(Mandatory=$true)][string]$Project,
    [string]$Topic,
    [string]$Profile
)
$ErrorActionPreference = 'Stop'
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCommand) { $pythonCommand = Get-Command py -ErrorAction SilentlyContinue }
if (-not $pythonCommand) { throw 'Install Python 3.10+ or run pipeline.py with your Python interpreter.' }
$pipelineArguments = @('-X', 'utf8', (Join-Path $PSScriptRoot 'pipeline.py'), $Action, '--project', $Project)
if ($Topic) { $pipelineArguments += @('--topic', $Topic) }
if ($Profile) { $pipelineArguments += @('--profile', $Profile) }
& $pythonCommand.Source @pipelineArguments
if ($LASTEXITCODE -ne 0) { throw "Pipeline exited with code $LASTEXITCODE" }
