param(
    [string] $ComfyRoot,
    [string] $ComfyUrl
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'pipeline-config.ps1')

$comfyRoot = Get-ComfyRoot -Explicit $ComfyRoot
$python = Join-Path $comfyRoot '.venv\Scripts\python.exe'
$main = Join-Path $comfyRoot 'main.py'

if (-not (Test-Path -LiteralPath $python)) {
    throw "ComfyUI Python environment is missing: $python"
}
if (-not (Test-Path -LiteralPath $main)) {
    throw "ComfyUI entry point is missing: $main"
}

$uri = [uri](Get-ComfyUrl -Explicit $ComfyUrl)

Set-Location -LiteralPath $comfyRoot
& $python $main --listen $uri.Host --port $uri.Port --lowvram @args
exit $LASTEXITCODE
