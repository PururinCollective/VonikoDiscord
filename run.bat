@echo off
rem Usage: run.bat --char all        every character together in this window, taking turns. Press Q to quit.
rem        run.bat --char sakurako   just one character (add --cli to chat with her in the terminal)
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    python -m venv .venv || exit /b 1
)
rem quick when everything is already installed; picks up packages added to requirements.txt
.venv\Scripts\python -m pip install -q --disable-pip-version-check -r requirements.txt
if /i "%~1"=="--char" title %~2
.venv\Scripts\python bot.py %*
