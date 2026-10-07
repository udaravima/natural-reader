@echo off
rem startup.cmd: run startup.ps1 (the Windows counterpart of startup.sh) without
rem changing the PowerShell execution policy. Same commands as startup.sh:
rem   init [podman or docker], up, up-with-dev-auth, down, help
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0startup.ps1" %*
exit /b %ERRORLEVEL%
