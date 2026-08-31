@echo off
setlocal EnableExtensions

set "PROJECT_DIR=%~dp0"
if "%PROJECT_DIR:~-1%"=="\" set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"
set "PYTHON_EXE=%PROJECT_DIR%\.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
    echo Virtual environment is missing. Running setup...
    call "%PROJECT_DIR%\setup.cmd"
    if errorlevel 1 exit /b 1
)

"%PYTHON_EXE%" -c "import flask, playwright, waitress" >nul 2>&1
if errorlevel 1 (
    echo Project dependencies changed. Running uv sync...
    call "%PROJECT_DIR%\setup.cmd"
    if errorlevel 1 exit /b 1
)

if not exist "%PROJECT_DIR%\.env" copy /Y "%PROJECT_DIR%\.env.example" "%PROJECT_DIR%\.env" >nul
if not exist "%PROJECT_DIR%\data" mkdir "%PROJECT_DIR%\data"
pushd "%PROJECT_DIR%" >nul || exit /b 1

echo Starting HH Market Radar in browser mode...
echo Dashboard: http://127.0.0.1:5050
echo Login and password are entered only on the official hh.ru page in Chrome.
echo Press Ctrl+C to stop the local server.
echo.

"%PYTHON_EXE%" "%PROJECT_DIR%\web_app.py"
set "EXIT_CODE=%ERRORLEVEL%"
popd

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Server stopped with error code %EXIT_CODE%.
)
endlocal & exit /b %EXIT_CODE%
