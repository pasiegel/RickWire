@echo off
rem Launch RickWire without a console window. First run: setup.bat
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" call "%~dp0setup.bat"
start "" ".venv\Scripts\pythonw.exe" "%~dp0run.py" %*
