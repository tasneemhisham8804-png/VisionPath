@echo off
REM VisionPath QA dashboard: double-click to open it in the browser.
REM Close this window to stop it.
title VisionPath QA dashboard
cd /d "%~dp0.."
if not exist ".venv\Scripts\activate.bat" (
  echo Could not find .venv in %CD%
  echo Create it first: python -m venv .venv
  pause
  exit /b 1
)
call ".venv\Scripts\activate.bat"
echo Opening the dashboard. Close this window to stop it.
streamlit run dashboard\app.py
pause
