@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Установка E9pass OCR (один раз, нужен интернет) ===
where python >nul 2>nul || (echo Python не найден. Установите Python 3.12 с python.org и отметьте "Add python.exe to PATH". & pause & exit /b 1)
if not exist .venv python -m venv .venv || (echo Не удалось создать .venv & pause & exit /b 1)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu || (echo Ошибка установки torch & pause & exit /b 1)
python -m pip install -r requirements.txt || (echo Ошибка установки зависимостей & pause & exit /b 1)
python tools\download_models.py || (echo Ошибка загрузки моделей & pause & exit /b 1)
echo.
echo Готово. Запускайте run.bat — дальше интернет не нужен.
pause
