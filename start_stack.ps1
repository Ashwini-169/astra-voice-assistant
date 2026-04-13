Param(
    [switch]$ServicesOnly,
    [switch]$ShowTtsLogs
)

function Wait-HttpHealthy {
    param(
        [string]$Name,
        [string]$Url,
        [int]$TimeoutSeconds = 120,
        [int]$IntervalSeconds = 2
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing $Url -TimeoutSec 5
            if ($response.StatusCode -eq 200) {
                Write-Host "$Name is healthy: $Url"
                return $true
            }
        }
        catch {
            # keep waiting
        }
        Start-Sleep -Seconds $IntervalSeconds
    }

    Write-Host "$Name did not become healthy in ${TimeoutSeconds}s: $Url"
    return $false
}

function Ensure-Service {
    param(
        [string]$Name,
        [string]$HealthUrl,
        [string]$PythonPath,
        [string[]]$ProcessArgs,
        [int]$TimeoutSeconds = 120,
        [string]$WindowStyle = "Minimized"
    )

    try {
        $probe = Invoke-WebRequest -UseBasicParsing $HealthUrl -TimeoutSec 3
        if ($probe.StatusCode -eq 200) {
            Write-Host "$Name already healthy: $HealthUrl"
            return $true
        }
    }
    catch {
        # not healthy, start below
    }

    Write-Host "Starting $Name..."
    if (-not $ProcessArgs -or $ProcessArgs.Count -eq 0) {
        Write-Host "ERROR: No process arguments provided for $Name"
        return $false
    }
    Start-Process -FilePath $PythonPath -ArgumentList $ProcessArgs -WindowStyle $WindowStyle
    return Wait-HttpHealthy -Name $Name -Url $HealthUrl -TimeoutSeconds $TimeoutSeconds
}

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root "venv\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "ERROR: Python not found at $python"
    Write-Host "Run .\\setup.ps1 first to create/install the local environment."
    return
}

Push-Location $root
$env:PYTHONUNBUFFERED = 1
$env:AI_ASSISTANT_TTS_BACKEND = "edge"

# Ensure Ollama
$ollamaExe = Get-Command ollama -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
if (-not $ollamaExe) {
    Write-Host "ERROR: Ollama not found on PATH. Install from https://ollama.ai"
    Pop-Location
    return
}

$ollamaOk = $false
try {
    $r = Invoke-WebRequest -UseBasicParsing http://127.0.0.1:11434/api/tags -TimeoutSec 3
    if ($r.StatusCode -eq 200) {
        $tags = $r.Content | ConvertFrom-Json
        Write-Host "Ollama already running on :11434 with $($tags.models.Count) model(s)"
        $ollamaOk = $true
    }
}
catch {
    # start below
}

if (-not $ollamaOk) {
    Write-Host "Starting Ollama server..."
    Start-Process -FilePath $ollamaExe -ArgumentList "serve" -WindowStyle Minimized
    $ollamaOk = Wait-HttpHealthy -Name "ollama" -Url "http://127.0.0.1:11434/api/tags" -TimeoutSeconds 45
}

if (-not $ollamaOk) {
    Write-Host "ERROR: Ollama did not become healthy."
    Pop-Location
    return
}

$whisperOk = Ensure-Service -Name "whisper" -HealthUrl "http://127.0.0.1:8001/health" -PythonPath $python -ProcessArgs @("-m", "uvicorn", "services.whisper_service:app", "--host", "127.0.0.1", "--port", "8001") -TimeoutSeconds 180
$llmOk = Ensure-Service -Name "llm" -HealthUrl "http://127.0.0.1:8002/health" -PythonPath $python -ProcessArgs @("-m", "uvicorn", "services.llm_service:app", "--host", "127.0.0.1", "--port", "8002") -TimeoutSeconds 180
$ttsWindow = if ($ShowTtsLogs) { "Normal" } else { "Minimized" }
$ttsOk = Ensure-Service -Name "tts" -HealthUrl "http://127.0.0.1:8003/health" -PythonPath $python -ProcessArgs @("-m", "uvicorn", "services.tts_service:app", "--host", "127.0.0.1", "--port", "8003") -TimeoutSeconds 120 -WindowStyle $ttsWindow
$intentOk = Ensure-Service -Name "intent" -HealthUrl "http://127.0.0.1:8004/health" -PythonPath $python -ProcessArgs @("-m", "uvicorn", "services.intent_service:app", "--host", "127.0.0.1", "--port", "8004") -TimeoutSeconds 120

if (-not ($whisperOk -and $llmOk -and $ttsOk -and $intentOk)) {
    Write-Host "One or more services are not ready."
    Pop-Location
    return
}

if (-not $ServicesOnly) {
    Start-Sleep -Seconds 1
    Write-Host "Starting duplex conversation mode..."
    & $python -m orchestrator.main --duplex
}

Pop-Location
