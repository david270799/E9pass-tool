"""Локальный OCR на базе EasyOCR. Изображения никуда не отправляются.

Модели берутся из папки ``models`` рядом с программой (или из
``E9PASS_MODELS_DIR``). Если их там нет, EasyOCR один раз скачивает модели
(только веса нейросети, без ваших данных) и дальше работает без интернета.
"""

import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image, ImageOps

from .parser import CardData, parse_card

try:  # фото с iPhone (.heic)
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp", ".heic"}
MODEL_FILES = ("craft_mlt_25k.pth", "korean_g2.pth")


def app_dir() -> Path:
    if getattr(sys, "frozen", False):  # собрано PyInstaller
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def models_dir() -> Path:
    return Path(os.environ.get("E9PASS_MODELS_DIR", app_dir() / "models"))


def load_image(path) -> Image.Image:
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)  # поворот по данным камеры телефона
    return img.convert("RGB")


def _resize(img: Image.Image, target: int = 1800) -> Image.Image:
    w, h = img.size
    scale = target / max(w, h)
    if 0.9 < scale < 1.1:
        return img
    return img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)


def crop_card(img: Image.Image) -> Image.Image:
    """Ищет карту на фото (самый большой четырехугольник) и выравнивает перспективу.

    Если карту найти не удалось, возвращает изображение без изменений.
    """
    try:
        import cv2
    except ImportError:
        return img
    arr = np.array(img)
    h, w = arr.shape[:2]
    scale = 800 / max(h, w)
    small = cv2.resize(arr, (round(w * scale), round(h * scale)))
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_RGB2GRAY), (5, 5), 0)
    edges = cv2.dilate(cv2.Canny(gray, 50, 150), np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    area_total = small.shape[0] * small.shape[1]
    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
        area = cv2.contourArea(c)
        if area < 0.2 * area_total or area > 0.98 * area_total:
            continue
        approx = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
        if len(approx) != 4:
            continue
        pts = approx.reshape(4, 2).astype("float32") / scale
        s, d = pts.sum(axis=1), np.diff(pts, axis=1).ravel()
        tl, br = pts[np.argmin(s)], pts[np.argmax(s)]
        tr, bl = pts[np.argmin(d)], pts[np.argmax(d)]
        width = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
        height = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
        ratio = max(width, height) / max(1, min(width, height))
        if not 1.3 < ratio < 1.9:  # у ID-карты соотношение сторон ~1.58
            continue
        dst = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], "float32")
        m = cv2.getPerspectiveTransform(np.array([tl, tr, br, bl]), dst)
        return Image.fromarray(cv2.warpPerspective(arr, m, (width, height)))
    return img


def group_lines(results) -> List[str]:
    """Собирает фрагменты EasyOCR в строки по вертикальному положению."""
    items = []
    for box, text, conf in results:
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        items.append((min(ys), max(ys), min(xs), text))
    items.sort(key=lambda t: (t[0] + t[1]) / 2)
    lines: List[List[Tuple[float, float, float, str]]] = []
    for it in items:
        center = (it[0] + it[1]) / 2
        if lines:
            last = lines[-1]
            top = sum(i[0] for i in last) / len(last)
            bottom = sum(i[1] for i in last) / len(last)
            if top <= center <= bottom:
                last.append(it)
                continue
        lines.append([it])
    return [" ".join(i[3] for i in sorted(line, key=lambda t: t[2])) for line in lines]


class CardReader:
    def __init__(self, gpu: bool = False):
        import easyocr  # тяжёлый импорт — только при создании

        mdir = models_dir()
        have_models = all((mdir / f).exists() for f in MODEL_FILES)
        kwargs = {"gpu": gpu, "verbose": False}
        if have_models:
            kwargs.update(model_storage_directory=str(mdir), download_enabled=False)
        self.reader = easyocr.Reader(["ko", "en"], **kwargs)

    def ocr_lines(self, img: Image.Image) -> List[str]:
        results = self.reader.readtext(np.array(img), paragraph=False)
        return group_lines(results)

    def read(self, path) -> Tuple[CardData, List[str]]:
        """Распознаёт файл и возвращает (поля карты, сырые строки текста)."""
        img = _resize(crop_card(load_image(path)))
        best: Optional[Tuple[CardData, List[str]]] = None
        # Если с первого раза номер не найден — пробуем повернуть изображение.
        for angle in (0, 90, 270, 180):
            rotated = img.rotate(angle, expand=True) if angle else img
            lines = self.ocr_lines(rotated)
            data = parse_card(lines)
            if best is None or data.filled_count() > best[0].filled_count():
                best = (data, lines)
            if data.reg_no and data.filled_count() >= 4:
                break
        return best
