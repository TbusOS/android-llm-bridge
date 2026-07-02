@echo off
rem One-click launcher for the alb device agent.
rem Fill in agent.conf (copy agent.conf.example) and double-click this file.
rem It finds Python 3.11+, installs the two dependencies on first run, and
rem starts the agent. Extra arguments are passed through to alb_agent.py.
rem Unattended runs (Task Scheduler): set ALB_AGENT_NO_PAUSE=1 in the task's
rem environment so an exit never waits for a keypress.
setlocal
cd /d "%~dp0"

rem -- locate Python 3.11+: plain `python` may be the Microsoft Store stub
rem    or an old install -- fall back to the py launcher.
set "PY=python"
%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 set "PY=py -3"
%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [run-agent] Python 3.11+ not found.
    echo [run-agent] Install it from https://www.python.org/downloads/
    echo [run-agent] and tick "Add python.exe to PATH" in the installer.
    goto :fail
)

if not exist agent.conf (
    echo [run-agent] agent.conf not found.
    echo [run-agent] Copy agent.conf.example to agent.conf, fill in
    echo [run-agent] hub_url + token, then run this again.
    goto :fail
)

rem -- first run: install websockets + pyserial if missing
%PY% -c "import websockets, serial" >nul 2>&1
if errorlevel 1 (
    echo [run-agent] installing dependencies -- first run only...
    %PY% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [run-agent] pip install failed -- check network / proxy and retry.
        goto :fail
    )
)

echo [run-agent] status page: http://127.0.0.1:8731 by default -- the agent
echo [run-agent] log below prints the actual URL.
%PY% alb_agent.py %*
set "RC=%errorlevel%"

rem -- keep the window open so exit reasons are readable on double-click
rem    (e.g. a rejected token stops the agent on purpose).
if defined ALB_AGENT_NO_PAUSE exit /b %RC%
pause
exit /b %RC%

:fail
if defined ALB_AGENT_NO_PAUSE exit /b 1
pause
exit /b 1
