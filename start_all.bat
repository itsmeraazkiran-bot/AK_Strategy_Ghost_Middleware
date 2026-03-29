@echo off
title System Ignition Sequence

:: 1. Start the NGINX Traffic Cop
cd C:\nginx-1.28.3
start nginx

:: 2. Start the FastAPI Risk Engine
cd C:\TradingBot
start run_engine.bat

:: 3. Start the Streamlit Dashboard
cd C:\TradingBot
start run_dashboard.bat

exit