@echo off
title AK Strategy Risk Engine (FastAPI)
color 0A

:loop
echo [ %time% ] Starting Risk Engine...
"C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe" -m uvicorn main:app --host 0.0.0.0 --port 8001 > C:\TradingBot\engine_log.txt 2>&1
echo.
echo [ %time% ] WARNING: ENGINE CRASH DETECTED! Restarting in 5 seconds...
timeout /t 5 >nul
goto loop