# Print `set "NAME=value"` lines for the API keys this run needs, read from the user environment.
#
# Why this exists: the keys are stored in the user environment (set with `setx`), and a batch file started
# from Explorer inherits whatever Explorer itself was started with, which may predate the key. Reading them
# explicitly makes the launcher work without a reboot, and keeps the `for /f` + `powershell -Command` quoting
# out of the .bat, where it is fragile across shells.
#
# Only names and lengths are ever printed by the launcher; the values go to `set` in the child process.

$names = @('DEEPSEEK_API_KEY', 'FIRECRAWL_API_KEY', 'YOUTUBE_API_KEY', 'GITHUB_TOKEN')
$lines = foreach ($n in $names) {
    $v = [Environment]::GetEnvironmentVariable($n, 'User')
    if (-not $v) { $v = [Environment]::GetEnvironmentVariable($n, 'Machine') }
    if ($v) { 'set "{0}={1}"' -f $n, $v }
}
$lines -join "`r`n"
