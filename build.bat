@echo off
setlocal
pushd "%~dp0" || goto directory_failed

set "_ipc_python=.venv\Scripts\python.exe"
set "PYTHONDONTWRITEBYTECODE=1"
if exist "%_ipc_python%" goto dependencies

echo Creating the IPC Nexus virtual environment...
where py >nul 2>&1
if errorlevel 1 goto try_python
py -3 -m venv .venv
if not errorlevel 1 if exist "%_ipc_python%" goto dependencies

:try_python
where python >nul 2>&1
if errorlevel 1 goto environment_failed
python -m venv .venv
if errorlevel 1 goto environment_failed
if not exist "%_ipc_python%" goto environment_failed

:dependencies
echo Checking desktop dependencies...
"%_ipc_python%" -m pip --disable-pip-version-check --no-input install -r requirements.txt
if errorlevel 1 goto dependencies_failed

echo Starting IPC Nexus...
"%_ipc_python%" -B main.py
set "_ipc_exit=%errorlevel%"
if not "%_ipc_exit%"=="0" goto launch_failed
popd
exit /b 0

:environment_failed
echo ERROR: Could not create .venv. Install Python 3 with the py launcher or add python.exe to PATH.
goto failed

:dependencies_failed
echo ERROR: Could not install the desktop dependencies. Check the Python environment and your package index connection.
goto failed

:launch_failed
echo ERROR: IPC Nexus could not start or exited with code %_ipc_exit%. Review the messages above.
goto pause_and_exit

:failed
set "_ipc_exit=1"

:pause_and_exit
echo(
pause
popd
exit /b %_ipc_exit%

:directory_failed
echo ERROR: Could not open the IPC Nexus project directory.
echo(
pause
exit /b 1
