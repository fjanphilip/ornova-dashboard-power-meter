@echo off
REM Setup awal di mini PC. Jalankan sekali. Argumen diteruskan ke setup_db.py, contoh:
REM   setup.bat --mysql-host 127.0.0.1 --mysql-user root
cd /d "%~dp0"
if not exist .venv (
    python -m venv .venv || goto :err
)
.venv\Scripts\python -m pip install -r requirements.txt || goto :err
.venv\Scripts\python setup_db.py %* || goto :err
echo.
echo Selesai. Jalankan: .venv\Scripts\python server.py --host 0.0.0.0 --port 8080
exit /b 0
:err
echo Setup gagal.
exit /b 1
