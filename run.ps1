# Delta — one-command launch.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $root

if (-not $env:DEEPSEEK_API_KEY) {
    $key = [Environment]::GetEnvironmentVariable("DEEPSEEK_API_KEY", "User")
    if ($key) {
        $env:DEEPSEEK_API_KEY = $key
    } else {
        Write-Host "DEEPSEEK_API_KEY is not set; Delta will start and show a setup notice." -ForegroundColor Yellow
    }
}

$py = $env:DELTA_PYTHON
if (-not $py) {
    $candidates = @(
        (Join-Path $root ".venv\Scripts\python.exe"),
        "D:\LMY\coding\aichat\_py\Scripts\python.exe",
        "python"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -eq "python") { $py = "python"; break }
        if (Test-Path $candidate) { $py = $candidate; break }
    }
}

if (-not $py) {
    Write-Host "No Python found. Set DELTA_PYTHON to a Python 3.11+ executable." -ForegroundColor Red
    Pop-Location
    exit 1
}

$env:PYTHONUTF8 = "1"
& $py -m delta @args
$code = $LASTEXITCODE
Pop-Location
exit $code
