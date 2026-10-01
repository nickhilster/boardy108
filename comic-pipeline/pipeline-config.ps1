# Shared settings resolution for the PowerShell launchers.
# Mirrors pipeline_config.py: explicit argument > environment variable >
# JSON config file > built-in default. Dot-source it:  . .\pipeline-config.ps1

function Get-PipelineConfigPath {
    if ($env:PANELFORGE_CONFIG) {
        if (-not (Test-Path -LiteralPath $env:PANELFORGE_CONFIG -PathType Leaf)) {
            throw "PANELFORGE_CONFIG points at '$($env:PANELFORGE_CONFIG)', which is not a file."
        }
        return $env:PANELFORGE_CONFIG
    }
    $candidates = @(
        (Join-Path $PSScriptRoot 'pipeline.config.json'),
        (Join-Path $HOME '.panelforge.json')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    return $null
}

function Get-PipelineSetting {
    param(
        [Parameter(Mandatory)][string] $Key,
        [string] $Explicit,
        [string] $Default
    )
    if ($Explicit) { return $Explicit }

    $envName = "PANELFORGE_$($Key.ToUpperInvariant())"
    $envValue = [Environment]::GetEnvironmentVariable($envName)
    if ($envValue) { return $envValue }

    $path = Get-PipelineConfigPath
    if ($path) {
        try {
            $config = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
        } catch {
            throw "$path is not valid JSON: $($_.Exception.Message)"
        }
        $value = $config.$Key
        if ($null -ne $value -and "$value" -ne '') { return "$value" }
    }

    if ($Default) { return $Default }

    $where = if ($path) { $path } else {
        'no config file found (looked for pipeline.config.json beside these scripts and ~/.panelforge.json)'
    }
    throw "'$Key' is not configured. Set $envName, or add `"$Key`" to a config file -- $where. See pipeline.config.example.json."
}

function Get-ComfyRoot {
    param([string] $Explicit)
    $root = Get-PipelineSetting -Key 'comfy_root' -Explicit $Explicit
    if (-not (Test-Path -LiteralPath $root -PathType Container)) {
        throw "comfy_root resolves to '$root', which is not a directory."
    }
    return $root
}

function Get-ComfyUrl {
    param([string] $Explicit)
    return Get-PipelineSetting -Key 'comfy_url' -Explicit $Explicit -Default 'http://127.0.0.1:8188'
}

function Get-DirectorModel {
    param([string] $Explicit)
    return Get-PipelineSetting -Key 'model' -Explicit $Explicit -Default 'qwen3-vl:8b'
}
