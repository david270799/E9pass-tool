"""Записывает номер сборки (переменная APP_VERSION) в e9pass/__init__.py — его видно в отчете OCR."""

import os
import re
from pathlib import Path

path = Path(__file__).resolve().parent.parent / "e9pass" / "__init__.py"
version = os.environ["APP_VERSION"]
path.write_text(re.sub(r'__version__ = ".*"', f'__version__ = "{version}"', path.read_text(encoding="utf-8")),
                encoding="utf-8")
print(f"version {version}")
