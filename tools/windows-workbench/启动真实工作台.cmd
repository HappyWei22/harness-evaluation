@echo off
echo DSH workbench: http://127.0.0.1:8766/#library
python -B -X utf8 "%~dp0workbench.py" --mode dsh --port 8766
if errorlevel 1 pause
