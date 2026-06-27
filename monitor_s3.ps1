# Season-3 run health snapshot (the prompt study). One status line: games saved,
# whether a python run is alive, and minutes since the last game completed.
# Note: S3 games are SLOW (~30-40 min each, statetrack writes a big ledger), so
# the stall threshold is set high (90 min) — a 30-min idle is NORMAL here, not a
# stall. Pass a target as the first arg (defaults to 30 for this batch; use 108
# for the full season) — it only sets the display denominator.
param([int]$target = 72)
$dir = "C:\files\howl-arena-benchmark\werewolf_pkg\games\season-3"
$files = Get-ChildItem "$dir\*.json" -ErrorAction SilentlyContinue
$n = ($files | Measure-Object).Count
$alive = if (Get-Process python -ErrorAction SilentlyContinue) { "RUNNING" } else { "STOPPED" }
$latest = $files | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($latest) {
    $last = $latest.LastWriteTime.ToString('HH:mm:ss')
    $idle = [math]::Round(((Get-Date) - $latest.LastWriteTime).TotalMinutes, 1)
} else { $last = "-"; $idle = "-" }
$verdict = if ($alive -eq "STOPPED" -and $n -lt $target) { "  <-- STOPPED, re-launch to resume" }
           elseif ($alive -eq "RUNNING" -and $idle -ne "-" -and $idle -gt 90) { "  <-- possible stall (idle > 90 min)" }
           else { "" }
Write-Output ("[{0}] saved {1}/{2} | run {3} | last game {4} ({5} min ago){6}" -f `
    (Get-Date -Format 'HH:mm:ss'), $n, $target, $alive, $last, $idle, $verdict)
