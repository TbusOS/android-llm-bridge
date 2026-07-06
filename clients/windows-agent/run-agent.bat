@echo off
rem One-click launcher for the alb device agent.
rem Every run re-checks the environment (Python / deps / config / adb) and
rem prints each result, then starts the agent. Extra arguments are passed
rem through to alb_agent.py.
rem Unattended runs (Task Scheduler): set ALB_AGENT_NO_PAUSE=1 in the task's
rem environment so an exit never waits for a keypress.
setlocal
cd /d "%~dp0"

echo [run-agent] environment check:

rem -- 1. Python 3.11+: plain `python` may be the Microsoft Store stub or an
rem       old install -- fall back to the py launcher.
set "PY=python"
%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 set "PY=py -3"
%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 (
    echo   [x] Python 3.11+ ... NOT FOUND
    echo.
    echo [run-agent] Install it from https://www.python.org/downloads/
    echo [run-agent] and tick "Add python.exe to PATH" in the installer.
    goto :fail
)
%PY% -c "import sys; print('  [v] Python ' + '.'.join(map(str, sys.version_info[:3])) + ' ... OK')"

rem -- 2. deps: websockets + pyserial. Missing -> auto-install, then re-check.
%PY% -c "import websockets, serial; print('  [v] websockets ' + getattr(websockets, '__version__', '?') + ' / pyserial ' + getattr(serial, '__version__', '?') + ' ... OK')" 2>nul
if errorlevel 1 (
    echo   [!] websockets / pyserial missing -- installing...
    %PY% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo   [x] pip install FAILED -- check network / proxy and retry.
        goto :fail
    )
    %PY% -c "import websockets, serial; print('  [v] websockets ' + getattr(websockets, '__version__', '?') + ' / pyserial ' + getattr(serial, '__version__', '?') + ' ... installed OK')" 2>nul
    if errorlevel 1 (
        echo   [x] dependencies still missing after install.
        goto :fail
    )
)

rem -- 3. config file
if not exist agent.conf (
    echo   [x] agent.conf ... NOT FOUND
    echo.
    echo [run-agent] Copy agent.conf.example to agent.conf, fill in
    echo [run-agent] hub_url + token, then run this again.
    goto :fail
)
echo   [v] agent.conf ... OK

rem -- 4. adb: optional -- serial/UART works without it.
where adb >nul 2>&1
if errorlevel 1 (
    echo   [!] adb ... not on PATH -- UART works, adb bridging disabled
) else (
    %PY% -c "import subprocess, csv, re; out = subprocess.run(['adb', 'devices'], capture_output=True, text=True, timeout=20).stdout; ds = [l.split('\t')[0] for l in out.splitlines()[1:] if '\t' in l]; print('  [v] adb ... OK, devices: ' + (', '.join(ds) if ds else 'NONE -- board not visible to adb yet')); rows = [] if ds else list(csv.reader(subprocess.run(['tasklist', '/fo', 'csv', '/nh'], capture_output=True, text=True, timeout=10).stdout.splitlines())); hits = [r[0] + ' pid=' + r[1] for r in rows if len(r) > 1 and r[0].lower().removesuffix('.exe') != 'adb' and re.search(r'(^|[^a-z0-9])adb([^a-z0-9]|$)', r[0].lower().removesuffix('.exe'))]; hits and print('  [!] another adb build is running and likely holds the exclusive USB'); hits and print('  [!] interface: ' + ', '.join(hits)); hits and print('  [!] fix: taskkill /f /pid <pid>  then: adb kill-server + adb devices')" 2>nul
    if errorlevel 1 echo   [v] adb ... OK - device query failed or timed out
)

rem -- 5. serial ports visible to this machine, for quick eyeballing.
%PY% -c "from serial.tools import list_ports; ps = [p.device for p in list_ports.comports()]; print('  [v] serial ports here: ' + (', '.join(ps) if ps else 'NONE -- check the USB-serial cable/driver'))" 2>nul

echo.
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
