@echo off
echo ===================================================
echo   RAFT BOOKING SYSTEM - LOCAL LAUNCHER
echo ===================================================
echo.

IF NOT EXIST "venv" (
    echo [INFO] Creating virtual environment...
    python -m venv venv
)

echo [INFO] Activating virtual environment...
call venv\Scripts\activate

echo [INFO] Installing/Updating dependencies...
pip install -r requirements.txt
IF %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to install dependencies.
    echo Please check your internet connection or permissions.
    pause
    exit /b
)

echo [INFO] Setting environment variables...
set FLASK_APP=app.py
set FLASK_DEBUG=1
set ADMIN_EMAIL=admin@rafting.com
set ADMIN_PASSWORD=admin123
set MONGO_URI=mongodb+srv://raftingadmin:rafting123@cluster0.mongodb.net/raft_booking?retryWrites=true&w=majority

echo.
echo [INFO] Starting Server...
echo [INFO] Open your browser to: http://127.0.0.1:5000
echo ===================================================
python app.py
pause
