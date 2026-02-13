@echo off
REM Start Playwright bot with venv and provided settings
cd /d "%~dp0\.."
IF NOT EXIST ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  py -3 -m venv .venv
)
call .venv\Scripts\activate
REM Install dependencies (first run)
pip install -r requirements.txt
REM Install Playwright browsers (run once; harmless if repeated)
python -m playwright install
REM Configure environment
set MEET_LINK=https://meet.google.com/wam-mbqm-axy
set DISPLAY_NAME=Meeting Assistant
set BACKEND_URL=http://localhost:5000/captions
set HEADLESS=false
set RUN_MINUTES=0
python -m bot.bot
