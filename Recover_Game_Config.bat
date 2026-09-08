@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
set "DOLLY_RECOVERY_LOG=%~dp0logs\Dolly_recovery.log"
> "%DOLLY_RECOVERY_LOG%" echo Deadlock Dolly recovery: %DATE% %TIME%
py -3 -c "import sys,struct;sys.exit(0 if sys.version_info.__ge__((3,10)) and struct.calcsize('P') == 8 else 1)" >> "%DOLLY_RECOVERY_LOG%" 2>&1
if not errorlevel 1 goto run_py
python -c "import sys,struct;sys.exit(0 if sys.version_info.__ge__((3,10)) and struct.calcsize('P') == 8 else 1)" >> "%DOLLY_RECOVERY_LOG%" 2>&1
if not errorlevel 1 goto run_python
echo Install 64-bit Python 3.10 or newer, then run this recovery again.
echo Recovery log: "%DOLLY_RECOVERY_LOG%"
pause
exit /b 1

:run_py
py -3 -u -m dolly.launcher --recover >> "%DOLLY_RECOVERY_LOG%" 2>&1
goto finished

:run_python
python -u -m dolly.launcher --recover >> "%DOLLY_RECOVERY_LOG%" 2>&1

:finished
set "DOLLY_EXIT_CODE=%ERRORLEVEL%"
type "%DOLLY_RECOVERY_LOG%"
echo Recovery log: "%DOLLY_RECOVERY_LOG%"
pause
exit /b %DOLLY_EXIT_CODE%
