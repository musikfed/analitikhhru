$ErrorActionPreference = "Stop"
$ProjectDir = $PSScriptRoot
$PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $PythonExe)) {
    & (Join-Path $ProjectDir "setup.ps1")
}
& $PythonExe -c "import flask, playwright, waitress" 2>$null
if ($LASTEXITCODE -ne 0) {
    & (Join-Path $ProjectDir "setup.ps1")
}
if (-not (Test-Path -LiteralPath (Join-Path $ProjectDir ".env"))) {
    Copy-Item -LiteralPath (Join-Path $ProjectDir ".env.example") -Destination (Join-Path $ProjectDir ".env")
}

New-Item -ItemType Directory -Force -Path (Join-Path $ProjectDir "data") | Out-Null
Set-Location -LiteralPath $ProjectDir
Write-Host "Dashboard: http://127.0.0.1:5050"
Write-Host "HH login and password are entered only in the official browser page."
& $PythonExe (Join-Path $ProjectDir "web_app.py")
exit $LASTEXITCODE
