@echo off
cd /d "%~dp0"
py -3 scripts\run.py %*
if %errorlevel% neq 0 exit /b %errorlevel%
exit /b 0
