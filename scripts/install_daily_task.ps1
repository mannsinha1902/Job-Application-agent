$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $PythonPath)) {
    throw "Project virtual environment not found at $PythonPath. Create .venv or update this script."
}

$Action = New-ScheduledTaskAction `
    -Execute $PythonPath `
    -Argument "-m job_agent.daily_discovery" `
    -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -Daily -At 8:00AM
Register-ScheduledTask `
    -TaskName "AI Job Application Agent - Discovery" `
    -Action $Action `
    -Trigger $Trigger `
    -Description "Discover public job feeds. This task does not submit applications." `
    -Force | Out-Null

Write-Host "Daily job discovery scheduled for 8:00 AM. No applications will be submitted."

