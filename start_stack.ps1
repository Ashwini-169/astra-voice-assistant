Param(
	[switch]$ServicesOnly
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

$python = "D:\program\conda\envs\ryzen-ai1.6\python.exe"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $root

$env:PYTHONUNBUFFERED = 1

# ── Ollama model directory (all models live here) ────────────────────
$env:OLLAMA_MODELS = "D:\program\model"

# ── Ensure Ollama is running with correct OLLAMA_MODELS ─────────────
$ollamaExe = Get-Command ollama -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
if (-not $ollamaExe) {
	Write-Host "ERROR: Ollama not found on PATH. Install from https://ollama.ai"
	Pop-Location
	return
}

$ollamaRunning = $false
try {
	$r = Invoke-WebRequest -UseBasicParsing http://127.0.0.1:11434/api/tags -TimeoutSec 3
	$body = $r.Content | ConvertFrom-Json
	if ($r.StatusCode -eq 200 -and $body.models.Count -gt 0) { $ollamaRunning = $true }
} catch {}

if (-not $ollamaRunning) {
	# Kill any existing Ollama that may be running without OLLAMA_MODELS
	Get-Process ollama -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
	Start-Sleep -Seconds 2
	Write-Host "Starting Ollama server (OLLAMA_MODELS=$env:OLLAMA_MODELS)..."
	Start-Process -FilePath $ollamaExe -ArgumentList "serve" -WindowStyle Minimized
	$ollamaOk = Wait-HttpHealthy -Name "ollama" -Url "http://127.0.0.1:11434/api/tags" -TimeoutSeconds 30
	if (-not $ollamaOk) {
		Write-Host "ERROR: Ollama did not start. Check ollama serve manually."
		Pop-Location
		return
	}
	# Verify models are visible
	try {
		$tags = (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:11434/api/tags -TimeoutSec 5).Content | ConvertFrom-Json
		Write-Host "Ollama models loaded: $($tags.models.Count) ($($tags.models.name -join ', '))"
	} catch {
		Write-Host "WARNING: Could not verify Ollama models"
	}
} else {
	Write-Host "Ollama already running on :11434 with $($body.models.Count) model(s)"
}

# ── TTS backend (edge = Microsoft Edge TTS, no server needed) ────────────────
$env:AI_ASSISTANT_TTS_BACKEND = "edge"

# Start Whisper service first (small model takes ~15s to load)
Start-Process -FilePath $python -ArgumentList "-m", "uvicorn", "services.whisper_service:app", "--host", "127.0.0.1", "--port", "8001" -WindowStyle Minimized
Start-Sleep -Seconds 5

# Start LLM service (connects to Ollama and warms model on startup)
Start-Process -FilePath $python -ArgumentList "-m", "uvicorn", "services.llm_service:app", "--host", "127.0.0.1", "--port", "8002" -WindowStyle Minimized

# Start TTS service
Start-Process -FilePath $python -ArgumentList "-m", "uvicorn", "services.tts_service:app", "--host", "127.0.0.1", "--port", "8003" -WindowStyle Minimized

# Start Intent service
Start-Process -FilePath $python -ArgumentList "-m", "uvicorn", "services.intent_service:app", "--host", "127.0.0.1", "--port", "8004" -WindowStyle Minimized

$whisperOk = Wait-HttpHealthy -Name "whisper" -Url "http://127.0.0.1:8001/health" -TimeoutSeconds 180
$llmOk = Wait-HttpHealthy -Name "llm" -Url "http://127.0.0.1:8002/health" -TimeoutSeconds 180
$ttsOk = Wait-HttpHealthy -Name "tts" -Url "http://127.0.0.1:8003/health" -TimeoutSeconds 120
$intentOk = Wait-HttpHealthy -Name "intent" -Url "http://127.0.0.1:8004/health" -TimeoutSeconds 120

if (-not ($whisperOk -and $llmOk -and $ttsOk -and $intentOk)) {
	Write-Host "One or more services are not ready. Please check Ollama/Piper and service logs."
	Pop-Location
	return
}

if (-not $ServicesOnly) {
	Start-Sleep -Seconds 2
	Write-Host "Starting duplex conversation mode..."
	& $python -m orchestrator.main --duplex
}

Pop-Location
