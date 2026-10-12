@echo off
rem Double-click: starts DuckStation with FF7 if it is not open, opens the control panel and starts Story mode.
rem Remove -Play to only open the panel.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-FF7.ps1" -Panel -Play
if errorlevel 1 pause
