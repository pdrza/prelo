@echo off
rem prelo-enviar: manda livros para o Kindle ou Kobo ligado no USB. O utilitario de verdade
rem e scripts\prelo-enviar.ps1; este atalho o roda pelo PowerShell do Windows.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\prelo-enviar.ps1" %*
exit /b %ERRORLEVEL%
