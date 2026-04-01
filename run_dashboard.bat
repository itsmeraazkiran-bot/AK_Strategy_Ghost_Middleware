@echo off
:loop
call venv\Scripts\activate
streamlit run dashboard.py --server.port 8501 --logger.level=error
echo UI crashed. Rebooting in 5 seconds...
timeout /t 5
goto loop