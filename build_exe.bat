@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Сборка папки dist\E9pay с E9pay.exe (Python на целевом ПК не нужен).
rem Сначала выполните setup.bat.
call .venv\Scripts\activate.bat || (echo Сначала запустите setup.bat & pause & exit /b 1)
python -m pip install pyinstaller
pyinstaller --noconfirm --windowed --name E9pay --icon e9pass\assets\logo.ico ^
  --add-data "e9pass\assets;e9pass\assets" ^
  --collect-all easyocr --collect-all pymupdf --collect-submodules pillow_heif ^
  main.py || (echo Ошибка сборки & pause & exit /b 1)
xcopy /E /I /Y models dist\E9pay\models >nul
if exist tools\UnRAR.exe xcopy /Y tools\UnRAR.exe dist\E9pay\tools\ >nul
echo.
echo Готово: dist\E9pay\E9pay.exe  (копируйте всю папку dist\E9pay)
pause
