@echo off
title Install Dependencies

echo ================================================
echo   Smart Product Search System - Install Script
echo ================================================
echo.

cd /d "%~dp0"

if not exist "venv\Scripts\pip.exe" (
    echo [INFO] Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment!
        pause
        exit /b 1
    )
)

echo [Step 1] Activating virtual environment...
call venv\Scripts\activate.bat

echo [Step 2] Upgrading pip...
venv\Scripts\python.exe -m pip install --upgrade pip

echo [Step 3] Installing dependencies...
venv\Scripts\pip install -r requirements.txt

if errorlevel 1 (
    echo [ERROR] Failed to install dependencies!
    pause
    exit /b 1
)

echo.
echo ================================================
echo [OK] All dependencies installed successfully!
echo ================================================
echo.
echo Installed packages:
venv\Scripts\pip list

echo.
pause