@echo off
rem Usage: run.bat --char sakurako   (or rin; add --cli to chat in the terminal). Press Q to quit.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    python -m venv .venv || exit /b 1
)
rem quick when everything is already installed; picks up packages added to requirements.txt
.venv\Scripts\python -m pip install -q --disable-pip-version-check -r requirements.txt
.venv\Scripts\python bot.py %*
