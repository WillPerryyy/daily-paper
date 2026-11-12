@echo off
rem Opens the paper. Double-click this, or pin it to the taskbar.
rem
rem The window that appears is the server - leave it open while reading and
rem close it when done. The edition is rebuilt on every page load, so the gate
rem is current to the last close and the headlines to the minute.
rem
rem "python" on this machine is a Microsoft Store stub that fails, so the real
rem interpreter is named outright. If Python is ever reinstalled elsewhere,
rem change this one line.
setlocal
set PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe
if not exist "%PY%" set PY=py
cd /d "%~dp0"
"%PY%" serve.py %*
if errorlevel 1 pause
