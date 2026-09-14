@echo off
title Preset Manager - oh-my-opencode-slim
cd /d "%~dp0"

rem Open the web UI 2 seconds after the server starts booting
start "" /min cmd /c "timeout /t 2 /nobreak >nul & start "" http://127.0.0.1:8765"

echo Preset Manager starting...
echo Web UI : http://127.0.0.1:8765
echo Config : %USERPROFILE%\.config\opencode\oh-my-opencode-slim.json
echo Close this window to stop the server.
echo.

python app.py

echo.
echo Server stopped.
pause
