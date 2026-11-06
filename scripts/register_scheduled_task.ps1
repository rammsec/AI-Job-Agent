param(
    [string]$TaskName = "AICareerAgentDailyScan",
    [string]$RunTime = "08:30",
    [int]$RepeatHours = 4
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $PythonExe)) {
    Write-Error "Virtualenv Python not found at $PythonExe"
    exit 1
}

$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "-m src.run_job_scan" `
    -WorkingDirectory $ProjectRoot

$Trigger = New-ScheduledTaskTrigger `
    -Daily `
    -At $RunTime

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Automatically ingests resumes and scans configured job sources (Adzuna, Greenhouse, Lever, RSS/JSON feeds) into the local Career Agent SQLite database." `
    -Force

Write-Host "Registered Windows Scheduled Task '$TaskName' to run daily at $RunTime." -ForegroundColor Green
Write-Host "To run it immediately for testing: Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Cyan

