@echo off
REM Start Flask backend with venv
cd /d "%~dp0\.."
IF NOT EXIST ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  py -3 -m venv .venv
)
call .venv\Scripts\activate
pip install -r requirements.txt
python backend\app.py
