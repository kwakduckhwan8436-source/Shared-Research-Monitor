@echo off
cd /d "%~dp0"
title Build EXE - Research Monitor
echo.
echo  ============================================================
echo    Build standalone EXE [source bundled as bytecode]
echo  ============================================================
echo.
echo  [1/4] Preparing virtual environment...
if not exist ".venv" ( python -m venv .venv )
call .venv\Scripts\activate.bat
echo  [2/4] Installing deps + PyInstaller...
python -m pip install --upgrade pip >nul
pip install -r requirements.txt
pip install pyinstaller
echo  [3/4] Cleaning old build...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
echo  [4/4] Building...
pyinstaller build.spec --clean --noconfirm
echo.
if exist "dist\ResearchMonitor.exe" (
    echo  [OK] Done: dist\ResearchMonitor.exe
) else (
    echo  [WARN] Build output not found. Check logs above.
)
echo.
pause
