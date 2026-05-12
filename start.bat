@echo off
title Smart Product Search System

echo ================================================
echo   Smart Product Search System - Start Script
echo ================================================
echo.

cd /d "%~dp0"

if not exist "venv\Scripts\activate.bat" (
    echo [INFO] Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment!
        echo Please check if Python is installed.
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created.
    echo.
)

echo [Step 1] Activating virtual environment...
call venv\Scripts\activate.bat
echo [OK] Virtual environment activated.
echo.

echo [Step 2] Checking dependencies...
pip show streamlit >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing dependencies...
    venv\Scripts\pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies!
        pause
        exit /b 1
    )
    echo [OK] Dependencies installed.
) else (
    echo [OK] Dependencies already installed.
)
echo.

echo [Step 3] Checking .env file...
if not exist ".env" (
    echo [WARN] .env file not found!
    if exist ".env.example" (
        echo [INFO] Creating .env from .env.example...
        copy .env.example .env >nul
        echo [OK] .env file created.
        echo.
        echo [IMPORTANT] Please edit .env file and set:
        echo   - ANTHROPIC_AUTH_TOKEN: Your API Key
        echo   - ANTHROPIC_BASE_URL: API endpoint (optional)
        echo   - ANTHROPIC_MODEL: Model name (optional)
        echo.
        echo Press any key to continue...
        pause >nul
    )
) else (
    echo [OK] .env file exists.
)
echo.

echo ================================================
echo [Step 4] Starting Streamlit application...
echo ================================================
echo.
echo [INFO] Application will open in browser
echo [INFO] Access URL: http://localhost:8501
echo [INFO] Press Ctrl+C to stop
echo.
echo Starting...
echo.

streamlit run app.py

echo.
echo ================================================
echo [INFO] Application stopped
echo ================================================
pause