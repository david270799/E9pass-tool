"""Обработка фото ID: выравнивание по 4 углам, фильтр «чистый скан», лист А4."""

from pathlib import Path
from typing import Optional, Sequence

import cv2
import numpy as np
from PIL import Image

A4_MM = (210, 297)
DPI = 300
CARD_RATIO = 85.6 / 54.0

FILTER_ORIGINAL, FILTER_CONTRAST, FILTER_CLEAN = "original", "contrast", "clean"


def warp_perspective(img: Image.Image, corners: Sequence[Sequence[float]]) -> Image.Image:
    """Вырезает четырехугольник (tl, tr, br, bl) и выпрямляет его в прямоугольник."""
    pts = np.array(corners, dtype="float32")
    tl, tr, br, bl = pts
    width = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
    height = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
    width, height = max(width, 10), max(height, 10)
    dst = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], "float32")
    m = cv2.getPerspectiveTransform(pts, dst)
    out = cv2.warpPerspective(np.array(img.convert("RGB")), m, (width, height), flags=cv2.INTER_CUBIC)
    result = Image.fromarray(out)
    if result.height > result.width:  # карта горизонтальная
        result = result.rotate(90, expand=True)
    return result


def apply_filter(img: Image.Image, mode: str, strength: int = 50) -> Image.Image:
    """strength 0..100 — сила фильтра."""
    if mode == FILTER_ORIGINAL:
        return img
    arr = np.array(img.convert("RGB"))
    if mode == FILTER_CONTRAST:
        lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=1.0 + strength / 25, tileGridSize=(8, 8))
        l = clahe.apply(l)
        out = cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2RGB)
        return Image.fromarray(out)
    # Чистый скан: убираем тени (деление на размытый фон) + адаптивный порог.
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    k = max(15, (min(gray.shape) // 8) | 1)
    background = cv2.medianBlur(cv2.dilate(gray, np.ones((7, 7), np.uint8)), k)
    norm = cv2.divide(gray, background, scale=255)
    block = max(11, (min(gray.shape) // 30) | 1)
    c = 4 + strength // 10
    thr = cv2.adaptiveThreshold(norm, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, c)
    # Смешиваем с нормализованным изображением, чтобы фото на карте не превратилось в пятно.
    alpha = strength / 100
    out = cv2.addWeighted(thr, alpha, norm, 1 - alpha, 0)
    return Image.fromarray(out).convert("RGB")


def a4_page(card: Image.Image, margin_mm: float = 10) -> Image.Image:
    """Лист А4 (книжный): белое поле, карта на всю ширину между полями, по центру, белое поле."""
    page_w = round(A4_MM[0] / 25.4 * DPI)
    page_h = round(A4_MM[1] / 25.4 * DPI)
    page = Image.new("RGB", (page_w, page_h), "white")
    if card.height > card.width:
        card = card.rotate(90, expand=True)
    margin = round(margin_mm / 25.4 * DPI)
    target_w = page_w - 2 * margin
    target_h = round(target_w * card.height / card.width)
    resized = card.resize((target_w, target_h), Image.LANCZOS)
    page.paste(resized, ((page_w - target_w) // 2, (page_h - target_h) // 2))
    return page


def save_a4_pdf(card: Image.Image, out_path: Path, margin_mm: float = 10) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    a4_page(card, margin_mm).save(out_path, "PDF", resolution=DPI)
    return out_path


def process_id_photo(src: Path, out_dir: Path, corners: Optional[Sequence] = None,
                     filter_mode: str = FILTER_CONTRAST, strength: int = 50,
                     margin_mm: float = 10, stem: str = "ID_front", rotate: int = 0,
                     make_pdf: bool = True):
    """Полный цикл для фото ID: (путь к обработанному PNG, путь к PDF на А4 или None).

    corners — в пикселях изображения после поворота rotate (градусы против часовой).
    """
    from .ocr.ocr_engine import find_card_corners, load_image

    img = load_image(src)
    if rotate % 360:
        img = img.rotate(rotate, expand=True)
    if corners is None:
        corners = find_card_corners(img)
    card = warp_perspective(img, corners) if corners is not None else img
    card = apply_filter(card, filter_mode, strength)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / f"{stem}_scan.png"
    card.save(png)
    pdf = save_a4_pdf(card, out_dir / f"{stem}_A4.pdf", margin_mm) if make_pdf else None
    return png, pdf
