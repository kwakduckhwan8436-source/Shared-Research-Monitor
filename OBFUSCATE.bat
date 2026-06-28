@echo off
cd /d "%~dp0"
title Obfuscate Source
python build_obfuscate.py
echo.
echo  Obfuscated copy created at ..\stock_reco_obf
echo  [Run it the same way: WEB_SERVER.bat or python launch.py]
echo.
pause
