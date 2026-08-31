$ErrorActionPreference = "Stop"
$ProjectDir = $PSScriptRoot
Set-Location -LiteralPath $ProjectDir

Write-Host "[1/4] Checking uv..."
$null = Get-Command uv -ErrorAction Stop
uv --version

Write-Host "[2/4] Locating installed Python 3.12..."
$PythonExe = (& py -3.12 -c "import sys; print(sys.executable)").Trim()
if (-not $PythonExe) { throw "Python 3.12 was not found." }
Write-Host "Python: $PythonExe"

Write-Host "[3/4] Creating .venv and syncing dependencies..."
uv sync --python $PythonExe

$VenvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "Virtual environment was not created: $VenvPython"
}

Write-Host "[4/4] Preparing local folders..."
$EnvFile = Join-Path $ProjectDir ".env"
if (-not (Test-Path -LiteralPath $EnvFile)) {
    Copy-Item -LiteralPath (Join-Path $ProjectDir ".env.example") -Destination $EnvFile
}
New-Item -ItemType Directory -Force -Path (Join-Path $ProjectDir "data") | Out-Null
Write-Host "Setup completed. Google Chrome or Microsoft Edge must be installed."
