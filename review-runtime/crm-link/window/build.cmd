@echo off
setlocal
cd /d "%~dp0"
set "framework=%SystemRoot%\Microsoft.NET\Framework64\v4.0.30319"
if not exist "%framework%\csc.exe" set "framework=%SystemRoot%\Microsoft.NET\Framework\v4.0.30319"
"%framework%\csc.exe" /nologo /target:exe /optimize+ /codepage:65001 /out:WindowShare.exe /reference:System.dll,System.Core.dll,System.Drawing.dll,System.Windows.Forms.dll,System.Web.Extensions.dll /reference:"%framework%\WPF\UIAutomationClient.dll" /reference:"%framework%\WPF\UIAutomationTypes.dll" /reference:"%framework%\WPF\WindowsBase.dll" /resource:window.cjs,window.cjs WindowShare.cs DesktopControl.cs
exit /b %errorlevel%
