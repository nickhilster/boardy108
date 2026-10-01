param(
    [string] $ComfyRoot,
    [string] $ComfyUrl,
    [string] $Model
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'pipeline-config.ps1')

$comfyRoot = Get-ComfyRoot -Explicit $ComfyRoot
$comfyUrl = Get-ComfyUrl -Explicit $ComfyUrl
$directorModel = Get-DirectorModel -Explicit $Model

$python = Join-Path $comfyRoot '.venv\Scripts\python.exe'
$requiredFiles = @(
    (Join-Path $comfyRoot 'main.py'),
    $python,
    (Join-Path $comfyRoot 'models\diffusion_models\flux-2-klein-4b-fp8.safetensors'),
    (Join-Path $comfyRoot 'models\text_encoders\qwen_3_4b.safetensors'),
    (Join-Path $comfyRoot 'models\vae\flux2-vae.safetensors')
)

$missing = @($requiredFiles | Where-Object { -not (Test-Path -LiteralPath $_) })
$cuda = $null
if (Test-Path -LiteralPath $python) {
    $cudaJson = & $python -c "import json,torch; print(json.dumps({'available':torch.cuda.is_available(),'device':torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,'cuda':torch.version.cuda}))"
    $cuda = $cudaJson | ConvertFrom-Json
}

$ollamaVersion = $null
$ollamaModels = @()
if (Get-Command ollama -ErrorAction SilentlyContinue) {
    $ollamaVersion = (ollama --version 2>&1 | Out-String).Trim()
    $ollamaModels = @(ollama list | Select-Object -Skip 1 | ForEach-Object { ($_ -split '\s+')[0] })
}

$comfyReachable = $false
try {
    $null = Invoke-RestMethod -Uri "$($comfyUrl.TrimEnd('/'))/system_stats" -TimeoutSec 2
    $comfyReachable = $true
} catch {
    $comfyReachable = $false
}

[pscustomobject]@{
    ConfigFile = (Get-PipelineConfigPath)
    ComfyRoot = $comfyRoot
    ComfyUrl = $comfyUrl
    DirectorModel = $directorModel
    MissingFiles = $missing
    CudaAvailable = $cuda.available
    CudaDevice = $cuda.device
    TorchCuda = $cuda.cuda
    OllamaVersion = $ollamaVersion
    DirectorModelInstalled = $ollamaModels -contains $directorModel
    ComfyUIReachable = $comfyReachable
} | ConvertTo-Json -Depth 4

if ($missing.Count -gt 0 -or -not $cuda.available) {
    exit 1
}
