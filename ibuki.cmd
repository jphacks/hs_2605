@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 exit /b 1

rem Prefer the same team environment as the Mac launcher, then the local one.
if not exist "..\env\Scripts\python.exe" goto check_local
"..\env\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 goto team_python
:check_local
if not exist ".venv\Scripts\python.exe" goto check_fallback
".venv\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 goto local_python
:check_fallback
if not exist ".venv-windows\Scripts\python.exe" goto check_system
".venv-windows\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 goto fallback_python
:check_system
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 goto py_launcher
python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 goto system_python
echo Python 3.10+ is required. Install Python from https://www.python.org/downloads/windows/
echo Enable the Python launcher or add Python to PATH, then start ibuki.cmd again.
exit /b 1

:team_python
"..\env\Scripts\python.exe" "pc\launch_windows.py" %*
exit /b %errorlevel%
:local_python
".venv\Scripts\python.exe" "pc\launch_windows.py" %*
exit /b %errorlevel%
:fallback_python
".venv-windows\Scripts\python.exe" "pc\launch_windows.py" %*
exit /b %errorlevel%
:py_launcher
py -3 "pc\launch_windows.py" %*
exit /b %errorlevel%
:system_python
python "pc\launch_windows.py" %*
exit /b %errorlevel%
