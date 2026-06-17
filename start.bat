@echo off
cd /d "%~dp0"
if not exist .venv (
    py -3 -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if not exist .env (
    copy .env.example .env >nul
    echo Created .env - edit DISCORD_TOKEN or paste it in the UI.
)
python main.py
pause
