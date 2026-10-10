@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Сборка папки dist\E9passOCR с E9passOCR.exe (Python на целевом ПК не нужен).
rem Сначала выполните setup.bat.
call .venv\Scripts\activate.bat || (echo Сначала запустите setup.bat & pause & exit /b 1)
python -m pip install pyinstaller
pyinstaller --noconfirm --windowed --name E9passOCR ^
  --collect-all easyocr --collect-submodules pillow_heif ^
  main.py || (echo Ошибка сборки & pause & exit /b 1)
xcopy /E /I /Y models dist\E9passOCR\models >nul
echo.
echo Готово: dist\E9passOCR\E9passOCR.exe  (копируйте всю папку dist\E9passOCR)
pause
