@echo off
setlocal
rem Starts Spiderweb with Python. "pythonw" first (Python on the PATH), else the "pyw" launcher Python's installer
rem always puts in Windows (Python left off the PATH: the installer's own default). Looked up by cmd itself, no
rem other program started first: a slow start lets Windows open Spiderweb's window behind the others.
for %%I in (pythonw.exe) do set "PY=%%~$PATH:I"
if defined PY goto pythonw
for %%I in (pyw.exe) do set "PY=%%~$PATH:I"
if defined PY goto pyw
echo Spiderweb needs Python 3 (3.10 or newer), and no Python was found on this PC.
echo Get it free from python.org, install it, then start Spiderweb again.
pause
exit /b 1
:pythonw
start "" "%PY%" "%~dp0spiderweb.py" %*
exit /b
:pyw
start "" "%PY%" -3 "%~dp0spiderweb.py" %*
