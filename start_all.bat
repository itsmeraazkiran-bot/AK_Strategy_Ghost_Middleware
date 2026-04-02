@echo off
title Robosh V6 Launcher
color 0A

echo ===================================================
echo      ⚡ Starting Robosh V6 Command Center ⚡
echo ===================================================
echo.

echo [1/3] Booting Execution Engine (main.py)...
start "Robosh Engine" cmd /c "run_engine.bat"

echo.
echo [2/3] Waiting 5 seconds for Engine Heartbeat to initialize...
timeout /t 5 /nobreak

echo.
echo [3/3] Launching Interactive Dashboard (dashboard.py)...
start "Robosh Dashboard" cmd /c "run_dashboard.bat"

echo.
echo ✅ System successfully launched! You can close this window.
exit