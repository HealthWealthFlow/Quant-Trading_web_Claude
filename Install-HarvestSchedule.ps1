# Register a Windows Scheduled Task that runs the QSD harvest unattended, once a day.
#
#   .\Install-HarvestSchedule.ps1 -Request "intraday breakout strategies" -Target 5
#   .\Install-HarvestSchedule.ps1 -Request "mean reversion in forex" -Target 10 -Time 02:30
#   .\Install-HarvestSchedule.ps1 -Remove          # unregister
#   .\Install-HarvestSchedule.ps1 -RunNow          # start it immediately as a test
#
# Why a schedule rather than a loop: the daily AI budget is a HARD cap ($1/day by default). A long-running process
# would have to sleep for hours to cross the reset, and a laptop that sleeps or reboots would silently stop
# harvesting. A daily task sidesteps both — each run resumes the same campaign, and `qsd harvest` skips documents that
# were already read and answers that are already cached, so nothing is paid for twice.

[CmdletBinding()]
param(
    [string]$Request = "",
    [int]$Target = 5,
    [int]$Rounds = 3,
    [int]$Docs = 8,
    [string]$Time = "06:00",
    [string]$TaskName = "QSD Harvest",
    [switch]$Remove,
    [switch]$RunNow
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$qsd = Join-Path $repo ".venv\Scripts\qsd.exe"

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed scheduled task '$TaskName'."
    } else {
        Write-Host "No scheduled task named '$TaskName'."
    }
    return
}

if (-not (Test-Path $qsd)) {
    throw "Could not find $qsd. Install first: python -m venv .venv ; .venv\Scripts\pip install -e `".[dev]`""
}

if ([string]::IsNullOrWhiteSpace($Request)) {
    $Request = Read-Host "What should it research (e.g. intraday breakout strategies in forex)"
}
if ([string]::IsNullOrWhiteSpace($Request)) { throw "A request is required." }

# The campaign keeps its own identity, so every run continues the same research instead of starting over.
$arguments = @(
    "harvest", "`"$Request`"",
    "--target", $Target,
    "--max-rounds", $Rounds,
    "--docs", $Docs,
    "--sleep", "0"
) -join " "

$action = New-ScheduledTaskAction -Execute $qsd -Argument $arguments -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
# Run whether or not the user is signed in is not set: QSD reads API keys from the user environment, so it must run
# as this user. StartWhenAvailable catches up a run missed while the machine was off.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Hours 6) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "QSD harvest: research until $Target promising idea(s), daily at $Time." | Out-Null

Write-Host "Scheduled '$TaskName' to run daily at $Time."
Write-Host "  command: $qsd $arguments"
Write-Host "  check:   Get-ScheduledTaskInfo -TaskName '$TaskName'"
Write-Host "  results: .venv\Scripts\qsd.exe score   (and the dashboard)"

if ($RunNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Started now as a test."
}
