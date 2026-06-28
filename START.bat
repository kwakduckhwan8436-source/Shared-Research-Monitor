@echo off
set PYTHONUTF8=1
cd /d "%~dp0"
title Research Monitor

echo.
echo  ============================================================
echo    Research Monitor - Start
echo  ============================================================
echo.

REM ---- 1) Check Python ----
where py >nul 2>nul
if %errorlevel%==0 set PY=py
if not %errorlevel%==0 set PY=python
%PY% --version >nul 2>nul
if errorlevel 1 goto NOPYTHON
echo  [1/3] Python OK
echo.

REM ---- 2) Prepare settings file ----
if exist ".env" goto HASENV
if not exist ".env.example" goto RUNSERVER
copy ".env.example" ".env" >nul
echo  [2/3] Created settings file: .env
echo.
echo    For live data you need FREE API keys. See API_KEYS.md
echo    Calendar, glossary and community work without keys.
echo.
goto RUNSERVER

:HASENV
echo  [2/3] Using existing settings
echo.

:RUNSERVER
echo  [3/3] Starting server...
echo        First run installs packages, please wait 1-3 minutes.
echo        Browser opens automatically when ready.
echo        To stop: close this window.
echo.
%PY% launch.py
echo.
echo  ============================================================
echo  Server stopped. If it closed too fast, read messages above.
echo  ============================================================
pause
goto END

:NOPYTHON
echo  [ERROR] Python is not installed.
echo.
echo    1. Go to https://www.python.org/downloads/
echo    2. Download and install Python
echo    3. IMPORTANT: check "Add Python to PATH" during install
echo    4. Then double-click START.bat again
echo.
pause
goto END

:END
