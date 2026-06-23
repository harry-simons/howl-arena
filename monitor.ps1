# Season-run health snapshot. Prints one status line: games saved, whether the
# run process is alive, and how long since the last game completed (a stall/death
# shows as STOPPED with <108 saved, or RUNNING with a high "idle" minutes).
$dir = "C:\files\howl-arena-benchmark\werewolf_pkg\games\season-1"
$files = Get-ChildItem "$dir\*.json" -ErrorAction SilentlyContinue
$n = ($files | Measure-Object).Count
$alive = if (Get-Process python -ErrorAction SilentlyContinue) { "RUNNING" } else { "STOPPED" }
$latest = $files | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($latest) {
    $last = $latest.LastWriteTime.ToString('HH:mm:ss')
    $idle = [math]::Round(((Get-Date) - $latest.LastWriteTime).TotalMinutes, 1)
} else { $last = "-"; $idle = "-" }
$verdict = if ($alive -eq "STOPPED" -and $n -lt 108) { "  <-- DIED, re-launch to resume" }
           elseif ($alive -eq "RUNNING" -and $idle -ne "-" -and $idle -gt 20) { "  <-- possible stall" }
           else { "" }
Write-Output ("[{0}] saved {1}/108 | run {2} | last game {3} ({4} min ago){5}" -f `
    (Get-Date -Format 'HH:mm:ss'), $n, $alive, $last, $idle, $verdict)
