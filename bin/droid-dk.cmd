@echo off
rem Launch droid through dk for GUI clients that cannot call the PowerShell
rem `droid` function. dk keeps stdout clean, so ACP JSON passes through.
rem Do not rename this file to droid.cmd: dk would resolve it as droid itself.
where /q pwsh.exe
if %errorlevel%==0 (
    pwsh.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0dk.ps1" run droid %*
) else (
    powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0dk.ps1" run droid %*
)
exit /b %errorlevel%
