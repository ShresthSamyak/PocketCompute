# Register PocketCompute to start automatically when you log in to Windows.
# Run once:  powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1
# Remove with:  Unregister-ScheduledTask -TaskName PocketCompute -Confirm:$false

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$start = Join-Path $PSScriptRoot "start.ps1"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"$start`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName "PocketCompute" -Action $action -Trigger $trigger `
    -Settings $settings -Description "Message your computer from your phone." -Force | Out-Null

Write-Host "PocketCompute will now start automatically at login." -ForegroundColor Green
Write-Host "Start it now with: scripts\start.ps1" -ForegroundColor Cyan
