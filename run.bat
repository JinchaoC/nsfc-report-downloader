@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   NSFC report downloader (single-file)
echo ============================================
echo.
python "%~dp0nsfc_report.py"
echo.
echo Press any key to close.
pause >nul
