@echo off
cd /d "%~dp0"
python --version >nul 2>&1
if errorlevel 1 goto launcher
set "PY=python"
goto build
:launcher
py -3 --version >nul 2>&1
if errorlevel 1 goto fail
set "PY=py -3"
:build
%PY% -m pip install --upgrade pyinstaller
if errorlevel 1 goto fail
%PY% -m PyInstaller --noconfirm --onefile --windowed --name SmartFileManager main.py
if errorlevel 1 goto fail
echo EXE: dist\SmartFileManager.exe
pause
exit /b 0
:fail
echo Build failed. Check Python installation and PATH.
pause
exit /b 1
