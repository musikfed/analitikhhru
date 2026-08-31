@echo off
setlocal EnableExtensions

set "PROJECT_DIR=%~dp0"
if "%PROJECT_DIR:~-1%"=="\" set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"
pushd "%PROJECT_DIR%" >nul || (
    echo ERROR: Cannot open project directory.
    exit /b 1
)

echo [1/4] Checking uv...
where uv >nul 2>&1
if errorlevel 1 (
    echo ERROR: uv was not found in PATH.
    echo Install uv and open a new terminal window.
    popd
    exit /b 1
)
uv --version

echo [2/4] Locating installed Python 3.12...
set "PYTHON_EXE="
for /f "usebackq delims=" %%I in (`py -3.12 -c "import sys; print(sys.executable)" 2^>nul`) do set "PYTHON_EXE=%%I"
if not defined PYTHON_EXE (
    echo ERROR: Python 3.12 was not found by the Windows py launcher.
    echo Run: py --list
    popd
    exit /b 1
)
echo Python: %PYTHON_EXE%

echo [3/4] Creating .venv and syncing dependencies...
uv sync --python "%PYTHON_EXE%"
if errorlevel 1 (
    echo ERROR: uv sync failed.
    popd
    exit /b 1
)
if not exist "%PROJECT_DIR%\.venv\Scripts\python.exe" (
    echo ERROR: Virtual environment was not created.
    popd
    exit /b 1
)

echo [4/4] Preparing local folders...
if not exist "%PROJECT_DIR%\.env" copy /Y "%PROJECT_DIR%\.env.example" "%PROJECT_DIR%\.env" >nul
if not exist "%PROJECT_DIR%\data" mkdir "%PROJECT_DIR%\data"

echo.
echo Setup completed successfully.
echo Google Chrome or Microsoft Edge must be installed.
echo Start the dashboard with: start.cmd
popd
endlocal
exit /b 0
