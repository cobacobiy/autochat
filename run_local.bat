@echo off
echo Menghentikan Docker (jika masih berjalan)...
docker compose down

echo.
echo Memeriksa Virtual Environment...
if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe -c "import sys" 2>nul
    if errorlevel 1 (
        echo Virtual Environment rusak. Menghapus dan membuat ulang...
        rmdir /s /q .venv
    )
)

if not exist ".venv\Scripts\activate.bat" (
    echo Membuat Virtual Environment baru...
    py -m venv .venv
)

echo.
echo Mengaktifkan Virtual Environment...
call .venv\Scripts\activate.bat

echo.
echo Menginstall dependensi...
pip install -r bot/requirements.txt

echo.
echo Menginstall Playwright Chromium...
playwright install chromium

echo.
echo Menjalankan Bot di Windows (Browser akan terbuka)...
set HEADLESS=false
set AI_PROVIDER=ollama
python bot/main.py

pause
