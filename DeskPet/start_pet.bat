@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Hoshimi Mi DeskPet Client

if not exist "node_modules\electron\" (
    echo [1/2] Installing dependencies via npmmirror...
    set ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
    call npm install --registry=https://registry.npmmirror.com
)

node --experimental-transform-types --no-warnings src/host-daemon.ts
pause
