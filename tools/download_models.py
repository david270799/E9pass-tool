"""Скачивает модели EasyOCR (корейский + английский) в папку models.

После этого программа работает полностью без интернета.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from e9pass.ocr.ocr_engine import MODEL_FILES, models_dir  # noqa: E402


def main():
    import easyocr

    target = models_dir()
    target.mkdir(parents=True, exist_ok=True)
    easyocr.Reader(["ko", "en"], gpu=False, model_storage_directory=str(target), verbose=False)
    missing = [f for f in MODEL_FILES if not (target / f).exists()]
    if missing:
        sys.exit(f"Не удалось скачать: {', '.join(missing)}")
    print(f"Модели сохранены в {target}")


if __name__ == "__main__":
    main()
