# Helpers shared by run.ps1 and metrics.ps1. Dot-source it.
# ASCII only: Windows PowerShell 5.1 reads BOM-less scripts as ANSI.
# Adapted from the smartcity agent (scripts/agent in that repository).

function Exec([scriptblock]$Block, [switch]$AllowFail) {
  # Native stderr must not become a terminating error under 5.1.
  $ErrorActionPreference = 'Continue'
  # Merged stderr arrives as ErrorRecords; print them as plain lines.
  & $Block | ForEach-Object { if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message } else { $_ } }
  if (-not $AllowFail -and $LASTEXITCODE -ne 0) { throw "Command failed with exit code ${LASTEXITCODE}: $Block" }
}

# gh prints JSON over several lines; 5.1's ConvertFrom-Json also emits arrays as one object.
function Get-GhJson([scriptblock]$Block) {
  $text = (Exec $Block) -join "`n"
  return , @($text | ConvertFrom-Json)
}

# Non-ASCII text in these ASCII-only scripts, e.g. U('M\u00e9tricas').
function U([string]$s) { return [regex]::Unescape($s) }

# Property access that tolerates missing members under Set-StrictMode.
function Get-Prop($Obj, [string]$Name) {
  if ($null -eq $Obj) { return $null }
  $p = $Obj.PSObject.Properties[$Name]
  if ($p) { return $p.Value } else { return $null }
}

# "Fase actual" and "Adelanto permitido" from MOSCOW.md; $null if absent.
function Get-PhaseConfigFrom([string]$File) {
  if (-not (Test-Path $File)) { return $null }
  $t = [IO.File]::ReadAllText($File, (New-Object System.Text.UTF8Encoding $false))
  if ($t -notmatch '\*\*Fase actual:\*\*\s*(\d+)') { return $null }
  $current = [int]$Matches[1]
  $ahead = if ($t -match '\*\*Adelanto permitido:\*\*\s*Fase\s*(\d+)') { [int]$Matches[1] } else { $null }
  return [pscustomobject]@{ current = $current; ahead = $ahead }
}
