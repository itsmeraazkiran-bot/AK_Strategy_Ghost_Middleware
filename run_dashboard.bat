@echo off
:loop
call venv\Scripts\activate
streamlit run dashboard.py --server.port 8501
echo UI crashed. Rebooting in 5 seconds...
timeout /t 5
goto loop