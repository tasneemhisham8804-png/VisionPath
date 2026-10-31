@echo off
REM VisionPath black box viewer.
REM Double-click: opens the newest black box snapshot.
REM Drag a .jsonl file onto this icon: opens that file.
title VisionPath black box viewer
cd /d "%~dp0.."
if not exist ".venv\Scripts\activate.bat" (
  echo Could not find .venv in %CD%
  pause
  exit /b 1
)
call ".venv\Scripts\activate.bat"
python -m replay_viewer %*
pause
