@echo off
rem prelo-lote: converte livros em lote. O utilitario de verdade e scripts\prelo-lote.ps1;
rem este atalho o roda pelo PowerShell do Windows, sem depender da politica de execucao.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\prelo-lote.ps1" %*
exit /b %ERRORLEVEL%
