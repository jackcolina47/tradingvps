@echo off
rem Arranca el puente y lo relanza si se cierra. Ejecutar desde la carpeta windows_bridge.
cd /d "%~dp0"
:loop
python mt_bridge.py
echo El puente se detuvo. Reiniciando en 5 segundos...
timeout /t 5 /nobreak >nul
goto loop
