@echo off
rem Usage: run.bat --char sakurako   (or rin; add --cli to chat in the terminal). Press Q to quit.
rem        run.bat --char all        starts every character (each config.<char>.json) in her own window
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    python -m venv .venv || exit /b 1
)
rem quick when everything is already installed; picks up packages added to requirements.txt
.venv\Scripts\python -m pip install -q --disable-pip-version-check -r requirements.txt

if /i "%~1"=="--char" if /i "%~2"=="all" goto all
if /i "%~1"=="--char" title %~2
.venv\Scripts\python bot.py %*
exit /b

:all
rem each window closes when she quits with Q, and stays open if she stops with an error
for %%f in (config.*.json) do (
    if /i not "%%~nf"=="config.example" (
        for /f "tokens=1,* delims=." %%a in ("%%~nf") do (
            echo Starting %%b...
            start "%%b" cmd /c ".venv\Scripts\python bot.py --char %%b || pause"
        )
    )
)
