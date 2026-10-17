@echo off
setlocal EnableExtensions
rem Launch droid through dk for GUI clients that cannot call the PowerShell
rem `droid` function. dk run inherits stdin/stdout/stderr, so ACP JSON is not
rem re-encoded by Windows PowerShell 5.1 or PowerShell 7.
rem Prefer a real pwsh 7. The WindowsApps alias is not a working shell; fall
rem back to Windows PowerShell 5.1.
rem Do not rename this file to droid.cmd: dk would resolve it as droid itself.
set "DK_PS="
where pwsh.exe >nul 2>&1
if errorlevel 1 goto :launch
for /f "delims=" %%I in ('where pwsh.exe 2^>nul') do (
    if not defined DK_PS (
        echo %%I | find /i "\WindowsApps\" >nul
        if errorlevel 1 set "DK_PS=%%I"
    )
)
:launch
if not defined DK_PS set "DK_PS=powershell.exe"
"%DK_PS%" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0dk.ps1" run droid %*
exit /b
