@echo off
rem prelo-lote: converte livros em lote. O utilitario de verdade e scripts\prelo-lote.ps1;
rem este atalho o roda pelo PowerShell do Windows, sem depender da politica de execucao.
rem Aberto de dentro do PowerShell 7, o do Windows herdaria a lista de modulos do 7 e
rem perderia comandos como o Get-FileHash; sem a variavel, ele monta a propria lista.
setlocal
set "PSModulePath="
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\prelo-lote.ps1" %*
exit /b %ERRORLEVEL%
