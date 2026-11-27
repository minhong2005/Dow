@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title Dow - China Windows
if not exist "%LOCALAPPDATA%\Dow CRM\Workstation\runtime\node.exe" (
  echo Please start this with the Windows user who installed Dow Workstation.
  pause
  exit /b 1
)
"%LOCALAPPDATA%\Dow CRM\Workstation\runtime\node.exe" "%~dp0window.cjs"
if errorlevel 1 pause
