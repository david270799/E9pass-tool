"""Скачивает модели OCR (RapidOCR / PaddleOCR: поиск строк, поворот, корейский + латиница) в папку models.

После этого программа работает полностью без интернета.
"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from e9pass.ocr.ocr_engine import MODEL_FILES, models_dir, rapidocr_params  # noqa: E402


def main():
    import numpy as np
    import rapidocr
    from rapidocr import RapidOCR

    target = models_dir()
    target.mkdir(parents=True, exist_ok=True)
    engine = RapidOCR(params=rapidocr_params())  # без моделей в target — скачает в папку пакета
    engine(np.full((64, 256, 3), 255, dtype=np.uint8))
    cache = Path(rapidocr.__file__).parent / "models"
    for name in MODEL_FILES:
        if not (target / name).exists() and (cache / name).exists():
            shutil.copy2(cache / name, target / name)
    missing = [f for f in MODEL_FILES if not (target / f).exists()]
    if missing:
        sys.exit(f"Не удалось скачать: {', '.join(missing)}")
    print(f"Модели сохранены в {target}")


if __name__ == "__main__":
    main()
