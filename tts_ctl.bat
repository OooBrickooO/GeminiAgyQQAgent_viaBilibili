@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
title Mijiang GPT-SoVITS-v5 Controller
"C:\path\to\GPT-SoVITS-v5\runtime\python.exe" -u "C:\path\to\GPT-SoVITS-v5\tts_manager.py" %*
