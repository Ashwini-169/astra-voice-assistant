# show_service_logs.ps1
# Opens all service log files in separate PowerShell windows with live tail

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $root "logs\dev-manager"

# Service log files
$services = @(
    @{Name="LLM Service"; File="llm.log"; Color="Cyan"},
    @{Name="TTS Service"; File="tts.log"; Color="Green"},
    @{Name="Whisper Service"; File="whisper.log"; Color="Yellow"},
    @{Name="Intent Service"; File="intent.log"; Color="Magenta"}
)

Write-Host "Opening service log windows..." -ForegroundColor White

foreach ($service in $services) {
    $logPath = Join-Path $logDir $service.File
    
    if (-not (Test-Path $logPath)) {
        Write-Host "Creating log file: $logPath" -ForegroundColor Gray
        New-Item -Path $logPath -ItemType File -Force | Out-Null
    }
    
    $title = $service.Name
    $color = $service.Color
    
    # PowerShell command to tail the log with color
    $command = @"
`$Host.UI.RawUI.WindowTitle = '$title'
Write-Host '========================================' -ForegroundColor $color
Write-Host ' $title Log Viewer' -ForegroundColor $color
Write-Host ' File: $($service.File)' -ForegroundColor $color
Write-Host '========================================' -ForegroundColor $color
Write-Host ''
Get-Content '$logPath' -Wait -Tail 50 | ForEach-Object {
    if (`$_ -match 'ERROR|error|Error|FAILED|failed') {
        Write-Host `$_ -ForegroundColor Red
    } elseif (`$_ -match 'WARNING|warning|Warning') {
        Write-Host `$_ -ForegroundColor Yellow
    } elseif (`$_ -match 'INFO|info|Info') {
        Write-Host `$_ -ForegroundColor $color
    } else {
        Write-Host `$_
    }
}
"@
    
    # Start new PowerShell window
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $command
    Start-Sleep -Milliseconds 300
}

Write-Host ""
Write-Host "All service log windows opened successfully!" -ForegroundColor Green
Write-Host "Press any key to close this window..." -ForegroundColor Gray
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
