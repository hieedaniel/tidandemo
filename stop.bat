@echo off
title Stop Streamlit Application

echo Stopping Streamlit application...

REM Kill streamlit process
taskkill /F /IM streamlit.exe >nul 2>&1
taskkill /F /IM python.exe /FI "WINDOWTITLE eq *streamlit*" >nul 2>&1

echo [OK] Application stopped.
pause