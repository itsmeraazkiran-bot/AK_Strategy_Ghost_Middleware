@echo off
:loop
call venv\Scripts\activate
python main.py
echo Engine crashed. Rebooting in 5 seconds...
timeout /t 5
goto loop