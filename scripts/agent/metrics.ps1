<#
.SYNOPSIS
  Metrics and circuit breaker of the unattended agent, computed only from GitHub:
  its PRs (agent/* branches), the backlog issues and the run log issue (label
  agent:bitacora).

.DESCRIPTION
  Prints a Markdown summary: progress by phase (MOSCOW.md) and agent metrics
  grouped by prompt version and by model. With -Publish it rewrites the metrics
  section of the log issue body (editing a body sends no notification) and, if a
  breaker threshold is crossed, opens an `agent:pausa` issue that stops the agent
  until it is closed. Only events after the last closed pause issue count, so
  closing it resets the breaker. See docs/agente.md.

  Adapted from the smartcity agent. ASCII only.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\agent\metrics.ps1 -Days 7
#>
param(
  [int]$Days = 30,
  [string]$Repo,
  [switch]$Publish,
  [int]$MaxBlockedInARow = 3,
  [int]$MaxRejectedInARow = 3,
  [int]$MaxFailedRunsInARow = 3,
  [int]$MaxDenialsPerSession = 15
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'common.ps1')

$Utf8 = New-Object System.Text.UTF8Encoding $false
$OutputEncoding = $Utf8
[Console]::OutputEncoding = $Utf8
$LabelLog = 'agent:bitacora'
$LabelPause = 'agent:pausa'
$StartMarker = '<!-- metrics:start -->'
$EndMarker = '<!-- metrics:end -->'
$RepoRoot = Join-Path $PSScriptRoot '..\..'

if (-not $Repo) {
  $url = [string](Exec { git -C $RepoRoot remote get-url origin })
  if ($url.Trim() -notmatch 'github\.com[:/](.+?)(\.git)?$') { throw "origin is not a GitHub URL: $url" }
  $Repo = $Matches[1]
}
$Since = [DateTime]::UtcNow.AddDays(-$Days)

function ToUtc($s) { if ($s) { return [DateTimeOffset]::Parse([string]$s).UtcDateTime } else { return $null } }
function Median($xs) {
  $a = @($xs | Where-Object { $null -ne $_ } | ForEach-Object { [double]$_ } | Sort-Object)
  if ($a.Count -eq 0) { return $null }
  $m = [int][math]::Floor($a.Count / 2)
  if ($a.Count % 2) { return $a[$m] } else { return ($a[$m - 1] + $a[$m]) / 2 }
}
function Sum($xs) { $a = @($xs | Where-Object { $null -ne $_ } | ForEach-Object { [double]$_ }); if ($a.Count) { return ($a | Measure-Object -Sum).Sum } else { return $null } }
function Pct($n, $d) { if ($d -gt 0) { return '{0:N0} %' -f (100.0 * $n / $d) } else { return '-' } }
function Num($x, [string]$f = '{0:N1}') { if ($null -eq $x) { return '-' } else { return $f -f $x } }
# Reads `name `value`` tags written by run.ps1 in PR footers and run log headers.
function Get-Tag([string]$Text, [string]$Name) { if ($Text -match "$Name ``([^``]+)``") { return $Matches[1] } else { return 'sin registro' } }

function Get-CiState($Rollup) {
  $checks = @($Rollup | Where-Object { $null -ne $_ })
  if ($checks.Count -eq 0) { return 'none' }
  $red = 'FAILURE', 'CANCELLED', 'TIMED_OUT', 'ACTION_REQUIRED', 'ERROR', 'STARTUP_FAILURE'
  foreach ($c in $checks) {
    $result = Get-Prop $c 'conclusion'; if (-not $result) { $result = Get-Prop $c 'state' }
    if ($red -contains [string]$result) { return 'red' }
  }
  foreach ($c in $checks) {
    $status = Get-Prop $c 'status'
    if ($status -and $status -ne 'COMPLETED') { return 'pending' }
  }
  return 'green'
}

# --- PRs opened by the agent ---------------------------------------------------
$prs = @((Get-GhJson { gh pr list --repo $Repo --state all --limit 300 --json number,headRefName,state,isDraft,createdAt,mergedAt,closedAt,additions,deletions,body,statusCheckRollup }) |
  ForEach-Object { $_ } |
  Where-Object { $_.headRefName -like 'agent/*' -and (ToUtc $_.createdAt) -ge $Since } |
  ForEach-Object {
    $created = ToUtc $_.createdAt
    $merged = ToUtc (Get-Prop $_ 'mergedAt')
    $body = [string](Get-Prop $_ 'body')
    [pscustomobject]@{
      number = $_.number
      kind = if ($_.headRefName -like 'agent/issue-*') { 'issue' } else { 'backlog' }
      prompt = Get-Tag $body 'prompts'
      model = Get-Tag $body 'modelo'
      state = [string]$_.state
      draft = [bool]$_.isDraft
      created = $created
      closed = ToUtc (Get-Prop $_ 'closedAt')
      hours = if ($merged) { ($merged - $created).TotalHours } else { $null }
      lines = [int]$_.additions + [int]$_.deletions
      ci = Get-CiState (Get-Prop $_ 'statusCheckRollup')
    }
  } | Sort-Object created)

function Get-PrRow([string]$Name, $Items) {
  $items = @($Items | Where-Object { $null -ne $_ })
  $merged = @($items | Where-Object { $_.state -eq 'MERGED' }).Count
  $closed = @($items | Where-Object { $_.state -eq 'CLOSED' }).Count
  return "| $Name | $($items.Count) | $merged | $closed | $(@($items | Where-Object { $_.state -eq 'OPEN' }).Count) | " +
    "$(Pct $merged ($merged + $closed)) | $(Num (Median ($items | ForEach-Object { $_.hours }))) | " +
    "$(@($items | Where-Object { $_.draft }).Count) | $(@($items | Where-Object { $_.ci -eq 'red' }).Count) | " +
    "$(Num (Median ($items | ForEach-Object { $_.lines })) '{0:N0}') |"
}

# --- Sessions and runs, from the run log comments -------------------------------
$logIssue = @((Get-GhJson { gh issue list --repo $Repo --label $LabelLog --state open --limit 1 --json number }) | ForEach-Object { $_ })
$sessions = New-Object System.Collections.ArrayList
$runs = New-Object System.Collections.ArrayList
$logNumber = $null
if ($logIssue.Count -gt 0) {
  $logNumber = [string]$logIssue[0].number
  $view = @(Get-GhJson { gh issue view $logNumber --repo $Repo --json comments })[0]
  foreach ($c in @($view.comments)) {
    $body = [string]$c.body
    $at = ToUtc $c.createdAt
    if ($body -notmatch '^### Corrida (\S+)' -or $at -lt $Since) { continue }
    $prompt = Get-Tag $body 'prompts'
    $model = Get-Tag $body 'modelo'
    [void]$runs.Add([pscustomobject]@{ at = $at; failed = $body -like '*La corrida fallo:*' })
    $header = $null
    foreach ($line in ($body -split "`r?`n")) {
      if ($line -notlike '|*') { $header = $null; continue }
      $cells = @(($line.Trim().Trim('|') -split '(?<!\\)\|') | ForEach-Object { $_.Trim() })
      if ($null -eq $header) { if ($cells[0] -eq 'Tarea') { $header = $cells }; continue }
      if ($cells[0] -like '---*') { continue }
      $row = @{}; for ($i = 0; $i -lt [math]::Min($header.Count, $cells.Count); $i++) { $row[$header[$i]] = $cells[$i] }
      $num = { param($v) if ($v -match '^-?[0-9]+([.,][0-9]+)?$') { [double]($v -replace ',', '.') } else { $null } }
      $result = [string]$row['Resultado']
      [void]$sessions.Add([pscustomobject]@{
        at = $at; prompt = $prompt; model = $model
        outcome = if ($result -like 'Bloqueado*') { 'blocked' } elseif ($result -like 'Reintento*') { 'retry' } else { 'done' }
        turns = & $num $row['Turnos']; minutes = & $num $row['Min']
        denials = & $num $row['Denegaciones']; usd = & $num $row['USD eq. API']
      })
    }
  }
}

function Get-SessionRow([string]$Name, $Items) {
  $items = @($Items | Where-Object { $null -ne $_ })
  $blocked = @($items | Where-Object { $_.outcome -eq 'blocked' }).Count
  $retried = @($items | Where-Object { $_.outcome -eq 'retry' }).Count
  return "| $Name | $($items.Count) | $blocked ($(Pct $blocked $items.Count)) | $retried | $(Num (Median ($items | ForEach-Object { $_.turns })) '{0:N0}') | " +
    "$(Num (Median ($items | ForEach-Object { $_.minutes }))) | $(Num (Sum ($items | ForEach-Object { $_.minutes }))) | " +
    "$(Num (Sum ($items | ForEach-Object { $_.denials })) '{0:N0}') | $(Num (Sum ($items | ForEach-Object { $_.usd })) '{0:N2}') |"
}

# --- Circuit breaker ----------------------------------------------------------
# Counts only what happened after the last pause issue was closed.
$lastResume = @((Get-GhJson { gh issue list --repo $Repo --label $LabelPause --state closed --limit 20 --json closedAt }) |
  ForEach-Object { $_ } | ForEach-Object { ToUtc $_.closedAt } | Sort-Object | Select-Object -Last 1)
$resetAt = if ($lastResume.Count -gt 0) { $lastResume[0] } else { [DateTime]::MinValue }

function Get-Streak($Items, [scriptblock]$IsBad) {
  $n = 0
  foreach ($x in @($Items | Where-Object { $null -ne $_ } | Sort-Object at -Descending)) { if (& $IsBad $x) { $n++ } else { break } }
  return $n
}
$recentSessions = @($sessions | Where-Object { $_.at -gt $resetAt -and $_.outcome -ne 'retry' })
$finished = @($prs | Where-Object { $_.state -ne 'OPEN' -and $_.closed -gt $resetAt } | ForEach-Object {
    [pscustomobject]@{ at = $_.closed; rejected = $_.state -eq 'CLOSED' } })
$recentRuns = @($runs | Where-Object { $_.at -gt $resetAt })

$alerts = @()
$blockedStreak = Get-Streak $recentSessions { param($s) $s.outcome -eq 'blocked' }
if ($blockedStreak -ge $MaxBlockedInARow) { $alerts += "$blockedStreak tareas seguidas terminaron bloqueadas (umbral $MaxBlockedInARow)." }
$rejectedStreak = Get-Streak $finished { param($p) $p.rejected }
if ($rejectedStreak -ge $MaxRejectedInARow) { $alerts += "$rejectedStreak PRs seguidos se cerraron sin mezclar (umbral $MaxRejectedInARow)." }
$failedStreak = Get-Streak $recentRuns { param($r) $r.failed }
if ($failedStreak -ge $MaxFailedRunsInARow) { $alerts += "$failedStreak corridas seguidas fallaron (umbral $MaxFailedRunsInARow)." }
$lastSession = @($recentSessions | Sort-Object at | Select-Object -Last 1)
if ($lastSession.Count -gt 0 -and $null -ne $lastSession[0].denials -and $lastSession[0].denials -ge $MaxDenialsPerSession) {
  $alerts += U("La \u00faltima sesi\u00f3n tuvo $($lastSession[0].denials) denegaciones de permisos (umbral $MaxDenialsPerSession).")
}

# --- Progress by phase (labels fase-N on backlog issues, see MOSCOW.md) -----------
$phase = Get-PhaseConfigFrom (Join-Path $RepoRoot 'MOSCOW.md')
$backlog = @((Get-GhJson { gh issue list --repo $Repo --label backlog --state all --limit 500 --json number,title,state,stateReason,labels }) |
  ForEach-Object { $_ } | Where-Object { [string](Get-Prop $_ 'stateReason') -ne 'NOT_PLANNED' })
$hasLabel = { param($i, $l) @($i.labels | ForEach-Object { $_.name }) -contains $l }
$phaseOut = @((U '## Avance por fase'), '')
if ($phase) { $phaseOut += (U "Fase actual: **$($phase.current)** $(if ($phase.ahead) { "(adelanto permitido: fase $($phase.ahead))" }). Se cambia en ``MOSCOW.md``."), '' }
$phaseOut += '| Fase | Cerrados | Abiertos | Humano | Bloqueados | Avance |', '|---|---|---|---|---|---|'
foreach ($n in 0..9) {
  $items = @($backlog | Where-Object { & $hasLabel $_ "fase-$n" })
  if ($items.Count -eq 0) { continue }
  $open = @($items | Where-Object { $_.state -eq 'OPEN' })
  $closed = $items.Count - $open.Count
  $human = @($open | Where-Object { & $hasLabel $_ 'humano' }).Count
  $blocked = @($open | Where-Object { & $hasLabel $_ 'agent:bloqueado' }).Count
  $mark = if ($phase -and $n -eq $phase.current) { " $([char]0x2190) actual" } else { '' }
  $phaseOut += "| $n$mark | $closed | $($open.Count) | $human | $blocked | $(Pct $closed $items.Count) |"
}
$waiting = @($backlog | Where-Object { $_.state -eq 'OPEN' -and ((& $hasLabel $_ 'humano') -or (& $hasLabel $_ 'agent:bloqueado')) } | Sort-Object number)
if ($waiting.Count -gt 0) {
  $phaseOut += '', '**Te toca (humano o bloqueado):**'
  $phaseOut += @($waiting | ForEach-Object { "- #$($_.number) $($_.title)" })
}

# --- Report -------------------------------------------------------------------
$out = @($StartMarker, '') + $phaseOut + @(
  '',
  (U "## M\u00e9tricas del agente (\u00faltimos $Days d\u00edas)"),
  '',
  "_Actualizado el $((Get-Date).ToString('yyyy-MM-dd HH:mm')) por ``scripts/agent/metrics.ps1``._",
  '',
  (U "**Freno autom\u00e1tico:** $(if ($alerts.Count) { 'activado. ' + ($alerts -join ' ') } else { 'sin alertas.' })"),
  '',
  '### PRs del agente',
  '',
  (U '| Grupo | PRs | Mezclados | Cerrados sin mezclar | Abiertos | Tasa de merge | Horas a merge (mediana) | Borradores | CI rojo | L\u00edneas (mediana) |'),
  '|---|---|---|---|---|---|---|---|---|---|',
  (Get-PrRow 'Todos' $prs),
  (Get-PrRow 'Issues' ($prs | Where-Object { $_.kind -eq 'issue' })),
  (Get-PrRow 'Backlog' ($prs | Where-Object { $_.kind -eq 'backlog' }))
)
foreach ($key in 'prompt', 'model') {
  $label = if ($key -eq 'prompt') { 'prompts' } else { 'modelo' }
  foreach ($v in @($prs | ForEach-Object { $_.$key } | Sort-Object -Unique)) {
    $out += Get-PrRow "$label ``$v``" ($prs | Where-Object { $_.$key -eq $v })
  }
}
$out += '', (U '### Sesiones (desde los comentarios de esta bit\u00e1cora)'), ''
$out += '| Grupo | Tareas | Bloqueadas | Reintentos | Turnos (mediana) | Min (mediana) | Min (total) | Denegaciones | USD eq. API |'
$out += '|---|---|---|---|---|---|---|---|---|'
$out += Get-SessionRow 'Todas' $sessions
foreach ($key in 'prompt', 'model') {
  $label = if ($key -eq 'prompt') { 'prompts' } else { 'modelo' }
  foreach ($v in @($sessions | ForEach-Object { $_.$key } | Sort-Object -Unique)) {
    $out += Get-SessionRow "$label ``$v``" ($sessions | Where-Object { $_.$key -eq $v })
  }
}
$out += '', (U ("Corridas fallidas: $(@($runs | Where-Object { $_.failed }).Count). **Tasa de merge** = mezclados / (mezclados + cerrados sin mezclar). " +
    "**CI rojo**: estado del CI en el \u00faltimo commit del PR. **Reintentos**: errores pasajeros (cuota, red), que no cuentan como bloqueo.")), $EndMarker
$report = $out -join "`n"
Write-Output $report

if ($Publish) {
  if ($logNumber) {
    $current = [string](@(Get-GhJson { gh issue view $logNumber --repo $Repo --json body })[0].body)
    $pattern = '(?s)' + [regex]::Escape($StartMarker) + '.*?' + [regex]::Escape($EndMarker)
    $new = if ($current -match $pattern) { [regex]::Replace($current, $pattern, { param($m) $report }) } else { $current.TrimEnd() + "`n`n" + $report }
    $file = Join-Path ([IO.Path]::GetTempPath()) "acr-metrics-$PID.md"
    [IO.File]::WriteAllText($file, $new, $Utf8)
    Exec { gh issue edit $logNumber --repo $Repo --body-file $file 2>&1 } | Out-Host
    Remove-Item $file -Force
  }
  else { Write-Host 'No open run log issue; metrics not published.' }

  $paused = @((Get-GhJson { gh issue list --repo $Repo --label $LabelPause --state open --limit 1 --json number }) | ForEach-Object { $_ })
  if ($alerts.Count -gt 0 -and $paused.Count -eq 0) {
    Exec { gh label create $LabelPause --repo $Repo --color fbca04 --description 'Un issue abierto con esta etiqueta pausa al agente' --force 2>&1 } | Out-Host
    $body = @(
      (U 'El freno autom\u00e1tico de `scripts/agent/metrics.ps1` paus\u00f3 al agente:'), ''
    ) + @($alerts | ForEach-Object { "- $_" }) + @(
      '', (U "Revisa la bit\u00e1cora (#$logNumber) y los PRs recientes. **Para reanudar, cierra este issue**: el freno solo cuenta lo que pase despu\u00e9s del cierre.")
    )
    $file = Join-Path ([IO.Path]::GetTempPath()) "acr-pause-$PID.md"
    [IO.File]::WriteAllText($file, ($body -join "`n"), $Utf8)
    Exec { gh issue create --repo $Repo --title (U 'Pausa autom\u00e1tica del agente') --label $LabelPause --body-file $file 2>&1 } | Out-Host
    Remove-Item $file -Force
  }
}
