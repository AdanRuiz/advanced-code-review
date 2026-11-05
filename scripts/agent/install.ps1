<#
.SYNOPSIS
  Registers (or removes) the Windows scheduled task that runs scripts/agent/run.ps1
  a few minutes after the current user logs on and then every hour while the
  session is open, so a merged PR is followed by the next task. Idle runs only
  query git and GitHub; they do not use Claude quota. No admin rights needed.

  Why at logon and not at boot: Claude Code and gh keep their logins in the
  user's profile, and a task that runs before anyone logs on would need the
  Windows password stored in the task. Logging on after boot starts it.

  Adapted from the smartcity agent. ASCII only.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\agent\install.ps1
  powershell -ExecutionPolicy Bypass -File scripts\agent\install.ps1 -EveryMinutes 30
  powershell -ExecutionPolicy Bypass -File scripts\agent\install.ps1 -Uninstall
#>
param(
  [switch]$Uninstall,
  [int]$DelayMinutes = 3,
  [int]$EveryMinutes = 60
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$TaskName = 'advanced-code-review-agent'

if ($Uninstall) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
  Write-Host "Removed scheduled task '$TaskName'."
  return
}

$script = Join-Path $PSScriptRoot 'run.ps1'
$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
$every = New-TimeSpan -Minutes $EveryMinutes
$atLogOn = New-ScheduledTaskTrigger -AtLogOn -User $user
$atLogOn.Delay = "PT${DelayMinutes}M"
# Repeat for as long as the session lasts (no duration = indefinitely).
$atLogOn.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval $every).Repetition
# A logon trigger only starts repeating at the next logon, so a task registered
# mid-session would sit idle until then. This one repeats from now on; the
# Interactive principal keeps it from running while logged off, and
# IgnoreNew drops a run that overlaps another.
$fromNow = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes($DelayMinutes) -RepetitionInterval $every
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Hours 4) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($atLogOn, $fromNow) -Settings $settings `
  -Principal $principal -Description 'Advanced Code Review: unattended Claude agent (docs/agente.md)' -Force | Out-Null
Write-Host "Registered '$TaskName': runs $script in $DelayMinutes min and $DelayMinutes min after $user logs on, then every $EveryMinutes min."
Write-Host "Run it now with: Start-ScheduledTask -TaskName $TaskName"
