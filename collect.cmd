@echo off
setlocal EnableExtensions
set "PROJECT_DIR=%~dp0"
if "%PROJECT_DIR:~-1%"=="\" set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"
set "PYTHON_EXE=%PROJECT_DIR%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" call "%PROJECT_DIR%\setup.cmd"
if errorlevel 1 exit /b 1
"%PYTHON_EXE%" -c "import playwright" >nul 2>&1
if errorlevel 1 call "%PROJECT_DIR%\setup.cmd"
if errorlevel 1 exit /b 1
if not exist "%PROJECT_DIR%\data" mkdir "%PROJECT_DIR%\data"
pushd "%PROJECT_DIR%" >nul || exit /b 1
"%PYTHON_EXE%" "%PROJECT_DIR%\hh_browser_collector.py" --collect --config "%PROJECT_DIR%\searches.json" --db "%PROJECT_DIR%\data\hh_market.sqlite3" --csv "%PROJECT_DIR%\data\hh_market.csv"
set "EXIT_CODE=%ERRORLEVEL%"
popd
if not "%EXIT_CODE%"=="0" pause
endlocal & exit /b %EXIT_CODE%
