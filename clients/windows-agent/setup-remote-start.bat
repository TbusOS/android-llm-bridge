@echo off
rem One-time setup so the Linux hub can start this agent over SSH:
rem authorizes the hub's public key and registers the "alb-agent" task.
rem Details and how to undo: README.md, "remote start".
rem Extra arguments go to setup_remote_start.py (e.g. --remove).
setlocal
cd /d "%~dp0"

set "PY=python"
%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 set "PY=py -3"
%PY% "%~dp0setup_remote_start.py" %*
set "RC=%errorlevel%"
echo.
pause
exit /b %RC%
