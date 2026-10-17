"""Скачивает модели OCR (RapidOCR / PaddleOCR: поиск строк, поворот, корейский + латиница) в папку models.

После этого программа работает полностью без интернета.
"""

import hashlib
import shutil
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from e9pass.ocr.ocr_engine import CLS_MODEL, DET_MODEL, REC_MODEL, models_dir  # noqa: E402

# Модель корейского распознавания не входит в пакет rapidocr — берем ее с хранилища RapidOCR
# (та же версия, что в requirements.txt) и проверяем контрольную сумму.
REC_URL = ("https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/PP-OCRv5/rec/"
           "korean_PP-OCRv5_rec_mobile.onnx")
REC_SHA256 = "cd6e2ea50f6943ca7271eb8c56a877a5a90720b7047fe9c41a2e541a25773c9b"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_rec(target: Path):
    dst = target / REC_MODEL
    if dst.exists() and sha256(dst) == REC_SHA256:
        return
    tmp = dst.with_suffix(".part")
    for attempt in range(1, 5):
        try:
            with urllib.request.urlopen(REC_URL, timeout=120) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f)
            break
        except OSError as e:
            print(f"Попытка {attempt}: {e}")
            if attempt == 4:
                raise
    if sha256(tmp) != REC_SHA256:
        tmp.unlink()
        sys.exit(f"{REC_MODEL}: контрольная сумма не совпала")
    tmp.replace(dst)


def main():
    import rapidocr

    target = models_dir()
    target.mkdir(parents=True, exist_ok=True)
    bundled = Path(rapidocr.__file__).parent / "models"  # поиск строк и поворот входят в пакет
    for name in (DET_MODEL, CLS_MODEL):
        if not (target / name).exists():
            shutil.copy2(bundled / name, target / name)
    download_rec(target)
    print(f"Модели сохранены в {target}")


if __name__ == "__main__":
    main()
