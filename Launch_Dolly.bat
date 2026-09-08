@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
set "DOLLY_STARTUP_LOG=%~dp0logs\Dolly_startup.log"
> "%DOLLY_STARTUP_LOG%" echo Deadlock Dolly startup: %DATE% %TIME%
echo Looking for 64-bit Python 3.10 or newer with Tk support...
py -3 -c "import sys,struct,tkinter;sys.exit(0 if sys.version_info.__ge__((3,10)) and struct.calcsize('P') == 8 else 1)" >> "%DOLLY_STARTUP_LOG%" 2>&1
if not errorlevel 1 goto run_py
python -c "import sys,struct,tkinter;sys.exit(0 if sys.version_info.__ge__((3,10)) and struct.calcsize('P') == 8 else 1)" >> "%DOLLY_STARTUP_LOG%" 2>&1
if not errorlevel 1 goto run_python
echo.
echo Python was not found, was not 64-bit, or is missing Tk support.
echo Install 64-bit Python 3.10 or newer from https://www.python.org/downloads/windows/
echo Select the Tcl/Tk option in the installer. No pip packages are needed.
echo Startup log: "%DOLLY_STARTUP_LOG%"
pause
exit /b 1

:run_py
rem Bootstrap owns the GUI log handle. Do not redirect this command to that log.
py -3 -u -m dolly.bootstrap
goto finished

:run_python
python -u -m dolly.bootstrap

:finished
set "DOLLY_EXIT_CODE=%ERRORLEVEL%"
if "%DOLLY_EXIT_CODE%"=="0" exit /b 0
echo.
echo Dolly could not start ^(error %DOLLY_EXIT_CODE%^).
echo Please send the startup log: "%DOLLY_STARTUP_LOG%"
type "%DOLLY_STARTUP_LOG%"
pause
exit /b %DOLLY_EXIT_CODE%
