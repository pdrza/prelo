@echo off
rem prelo-vigiar: converte os livros que chegam numa pasta. O utilitario de verdade e
rem scripts\prelo-vigiar.ps1; este atalho o roda pelo PowerShell do Windows.
rem Sem a lista de modulos herdada (do PowerShell 7, por exemplo): ver prelo-lote.cmd.
setlocal
set "PSModulePath="
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\prelo-vigiar.ps1" %*
exit /b %ERRORLEVEL%
