Param(
    [switch]$PullModel
)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $root "venv\python.exe"

Push-Location $root
$hfCacheRoot = Join-Path $root ".hf_cache"
$hfHubCache = Join-Path $hfCacheRoot "hub"
if (-not (Test-Path $hfCacheRoot)) { New-Item -Path $hfCacheRoot -ItemType Directory | Out-Null }
if (-not (Test-Path $hfHubCache)) { New-Item -Path $hfHubCache -ItemType Directory | Out-Null }
$env:HF_HOME = $hfCacheRoot
$env:HUGGINGFACE_HUB_CACHE = $hfHubCache

if (-not (Test-Path $venvPython)) {
    Write-Host "Creating venv at .\venv ..."
    python -m venv venv
}

if (-not (Test-Path $venvPython)) {
    Write-Host "ERROR: Could not create virtual environment."
    Pop-Location
    return
}

Write-Host "Installing dependencies from requirements.txt ..."
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements.txt

Write-Host "Checking Ollama ..."
$ollamaExe = Get-Command ollama -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
if (-not $ollamaExe) {
    Write-Host "WARNING: Ollama not found on PATH. Install from https://ollama.ai"
    Pop-Location
    return
}

try {
    Invoke-WebRequest -UseBasicParsing http://127.0.0.1:11434/api/tags -TimeoutSec 3 | Out-Null
}
catch {
    Write-Host "Starting Ollama server ..."
    Start-Process -FilePath $ollamaExe -ArgumentList "serve" -WindowStyle Minimized
    Start-Sleep -Seconds 3
}

if ($PullModel) {
    Write-Host "Ensuring model qwen2.5:3b is available ..."
    ollama pull qwen2.5:3b
} else {
    Write-Host "Tip: run '.\setup.ps1 -PullModel' to pre-download qwen2.5:3b."
}

Write-Host "Setup complete."
Write-Host "HF cache directory: $hfCacheRoot (reused across runs; avoids re-downloading models)."
Write-Host "Next: .\start_stack.ps1"

Pop-Location
