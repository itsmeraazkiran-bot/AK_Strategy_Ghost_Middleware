@echo off
title Robosh V6 Ignition Sequence

:: 1. Start the FastAPI Engine (Ngrok Auto-Starts Inside)
cd C:\TradingBot
start run_engine.bat

:: 2. Start the Streamlit Command Center
cd C:\TradingBot
start run_dashboard.bat

exit