@echo off
title Project Syndicate — Command Center
cd /d "%~dp0"
.venv\Scripts\python.exe scripts\syndicate_cli.py
pause
