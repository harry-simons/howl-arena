# Season-2 run health snapshot (the quant study). One status line: games saved,
# whether a python run is alive, and minutes since the last game completed (a
# stall/death shows as STOPPED with <72 saved, or RUNNING with high "idle" mins).
$target = 108
$dir = "C:\files\howl-arena-benchmark\werewolf_pkg\games\season-2"
$files = Get-ChildItem "$dir\*.json" -ErrorAction SilentlyContinue
$n = ($files | Measure-Object).Count
$alive = if (Get-Process python -ErrorAction SilentlyContinue) { "RUNNING" } else { "STOPPED" }
$latest = $files | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($latest) {
    $last = $latest.LastWriteTime.ToString('HH:mm:ss')
    $idle = [math]::Round(((Get-Date) - $latest.LastWriteTime).TotalMinutes, 1)
} else { $last = "-"; $idle = "-" }
$verdict = if ($alive -eq "STOPPED" -and $n -lt $target) { "  <-- DIED, re-launch to resume" }
           elseif ($alive -eq "RUNNING" -and $idle -ne "-" -and $idle -gt 20) { "  <-- possible stall" }
           else { "" }
Write-Output ("[{0}] saved {1}/{2} | run {3} | last game {4} ({5} min ago){6}" -f `
    (Get-Date -Format 'HH:mm:ss'), $n, $target, $alive, $last, $idle, $verdict)
