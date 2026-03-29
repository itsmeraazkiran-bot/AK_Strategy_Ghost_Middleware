@echo off
title AK Strategy Command Center (Streamlit)
color 0B

:loop
echo [ %time% ] Starting Streamlit Dashboard...
"C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe" -m streamlit run dashboard.py --server.port 8501 --server.address 0.0.0.0 --server.headless true
echo.
echo [ %time% ] WARNING: DASHBOARD CRASH DETECTED! Restarting in 5 seconds...
timeout /t 5 >nul
goto loop