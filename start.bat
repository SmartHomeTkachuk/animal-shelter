@echo off
chcp 65001 >nul
title Animal Shelter Bot + Parser
cls
echo ==================================================
echo        ANIMAL SHELTER BOT + SITE PARSER
echo ==================================================
echo.

if not exist requirements.txt (
    echo ERROR: requirements.txt not found!
    pause
    exit /b 1
)
if not exist bot.py (
    echo ERROR: bot.py not found!
    pause
    exit /b 1
)

echo Installing dependencies...
pip install -r requirements.txt

if errorlevel 1 (
    echo ERROR: Failed to install dependencies!
    pause
    exit /b 1
)

echo.
echo Running site parser (if needed)...
python site_parser.py

echo.
echo Starting bot...
echo ==================================================
python bot.py

echo.
echo ==================================================
echo Bot stopped. Press any key to exit.
pause