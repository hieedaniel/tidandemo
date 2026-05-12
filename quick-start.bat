@echo off
title Quick Start - No Checks

cd /d "%~dp0"

if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else (
    echo [ERROR] Virtual environment not found!
    echo Please run start.bat first.
    pause
    exit /b 1
)

echo Starting Smart Product Search System...
echo Access URL: http://localhost:8501
echo.
streamlit run app.py

pause