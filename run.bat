@echo off
chcp 65001 >nul
cd /d "%~dp0"
title COMSC4 DEBSIRIN Server

call :findpy
if not defined PY (
    echo [ERROR] Python not found. Please install Python 3.8+ first.
    pause
    exit /b 1
)

echo ============================================================
echo   COMSC4 DEBSIRIN - Computational Science Learning System
echo ============================================================
echo [Python] Using %PY%
%PY% --version

:: Stop any previous runserver occupying port 8000 (e.g. old DSCOMPOSN session)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000" ^| findstr "LISTENING"') do (
    echo [Port 8000] Stopping previous server process PID %%a...
    taskkill /F /T /PID %%a >nul 2>&1
)

:: Check required packages
%PY% -c "import django, openpyxl, PIL, cv2" >nul 2>&1
if errorlevel 1 (
    echo [1/3] Installing requirements...
    %PY% -m pip install -q -r requirements.txt
) else (
    echo [1/3] Requirements already satisfied.
)

echo [2/3] Applying database migrations and seeding initial Debsirin student data...
%PY% manage.py makemigrations core --noinput
%PY% manage.py migrate --noinput
%PY% -c "import os, django; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'comsc4.settings'); django.setup(); from core.services import seed_initial_comsc4_data; seed_initial_comsc4_data(); print('Initial COMSC4 data ready!')"

echo [3/3] Starting COMSC4 server at http://127.0.0.1:8000/ (Press Ctrl+C to stop)
echo ------------------------------------------------------------
echo   Admin Login     : admin / 1234
echo   Teacher Login   : teacher / 1234
echo   Special Student : 99999 / 1234 (Somchai Jaidee M.4/1)
echo   Student OAuth   : s51443@debsirin.ac.th
echo ------------------------------------------------------------
start "" http://127.0.0.1:8000/
%PY% manage.py runserver 127.0.0.1:8000
pause
exit /b

:findpy
set PY=
if exist "%~dp0venv\Scripts\python.exe" set PY="%~dp0venv\Scripts\python.exe" & exit /b
if exist "%~dp0.venv\Scripts\python.exe" set PY="%~dp0.venv\Scripts\python.exe" & exit /b
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe" & exit /b
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python311\python.exe" & exit /b
if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python310\python.exe" & exit /b
if exist "%LOCALAPPDATA%\Programs\Python\Python39\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python39\python.exe" & exit /b
if exist "%LOCALAPPDATA%\Programs\Python\Python38\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python38\python.exe" & exit /b
py --version >nul 2>&1 && set PY=py & exit /b
python -c "import sys" >nul 2>&1 && set PY=python & exit /b
for /d %%D in (C:\Python3*) do ( if exist "%%D\python.exe" set PY="%%D\python.exe" & exit /b )
for /d %%D in ("%ProgramFiles%\Python3*") do ( if exist "%%D\python.exe" set PY="%%D\python.exe" & exit /b )
exit /b
