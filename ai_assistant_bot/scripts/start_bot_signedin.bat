@echo off
REM Start Playwright bot with a persistent profile for Google sign-in
cd /d "%~dp0\.."
IF NOT EXIST ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  py -3 -m venv .venv
)
call .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install
REM Configure environment for signed-in mode
set MEET_LINK=https://meet.google.com/wam-mbqm-axy
set DISPLAY_NAME=Meeting Assistant
set BACKEND_URL=http://localhost:5000/captions
set HEADLESS=false
set RUN_MINUTES=0
set USER_DATA_DIR=.\user-data
echo If this is your first run, sign in to your Google account in the opened browser window.
python -m bot.bot
