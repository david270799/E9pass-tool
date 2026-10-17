@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
rem Локальная сборка (то же делает GitHub Actions). Сначала выполните setup.bat.
rem Нужен Inno Setup 6 (https://jrsoftware.org/isdl.php) для файла установщика.
call .venv\Scripts\activate.bat || (echo Сначала запустите setup.bat & pause & exit /b 1)
python -m pip install pyinstaller
pyinstaller --noconfirm --windowed --name E9pay --icon e9pass\assets\logo.ico ^
  --add-data "e9pass\assets;e9pass\assets" ^
  --collect-all rapidocr --collect-all onnxruntime --collect-all pymupdf --collect-all pikepdf --collect-all pyzipper ^
  --collect-submodules pillow_heif ^
  --exclude-module tkinter --exclude-module matplotlib --exclude-module torch ^
  main.py || (echo Ошибка сборки & pause & exit /b 1)
xcopy /E /I /Y models dist\E9pay\models >nul
xcopy /E /I /Y extension dist\E9pay\extension >nul
xcopy /I /Y tests\fixtures\sample_front.jpg dist\E9pay\selftest\ >nul
if exist tools\UnRAR.exe xcopy /Y tools\UnRAR.exe dist\E9pay\tools\ >nul
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" (
  "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" installer\E9pay.iss
  echo Готово: dist\E9pay-Setup-*.exe
) else (
  echo Готово: dist\E9pay\E9pay.exe ^(для установщика поставьте Inno Setup 6^)
)
pause
