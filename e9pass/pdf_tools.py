"""PDF: снятие пароля, текст для классификатора, склейка, картинки -> PDF."""

import shutil
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import pikepdf
import pymupdf

from .ocr.ocr_engine import IMAGE_EXTENSIONS


def birth_passwords(birth_yymmdd: str, arc: str = "") -> List[str]:
    """YYMMDD и YYYYMMDD из даты рождения (век — по 7-й цифре ARC, иначе по году)."""
    d = "".join(ch for ch in birth_yymmdd if ch.isdigit())
    if len(d) != 6:
        return []
    digits = "".join(ch for ch in arc if ch.isdigit())
    if len(digits) == 13 and digits[6] in "5678":
        century = "19" if digits[6] in "56" else "20"
    else:
        century = "20" if int(d[:2]) <= 30 else "19"
    return [d, century + d]


def is_encrypted(path: Path) -> bool:
    try:
        with pikepdf.open(path):
            return False
    except pikepdf.PasswordError:
        return True


def try_unlock(src: Path, dst: Path, passwords: Iterable[str]) -> Tuple[bool, Optional[str]]:
    """Пробует пароли; при успехе сохраняет PDF без пароля в dst. (успех, подошедший пароль)."""
    src, dst = Path(src), Path(dst)
    try:
        with pikepdf.open(src) as pdf:  # пароля нет
            if src != dst:
                shutil.copyfile(src, dst)
            return True, None
    except pikepdf.PasswordError:
        pass
    for pw in passwords:
        if not pw:
            continue
        try:
            with pikepdf.open(src, password=pw) as pdf:
                tmp = dst.with_suffix(".tmp.pdf")
                pdf.save(tmp)
            tmp.replace(dst)
            return True, pw
        except pikepdf.PasswordError:
            continue
    return False, None


def pdf_text(path: Path, max_pages: int = 2) -> str:
    try:
        with pymupdf.open(path) as doc:
            if doc.needs_pass:
                return ""
            return "\n".join(doc[i].get_text() for i in range(min(max_pages, doc.page_count)))
    except Exception:  # noqa: BLE001
        return ""


def pdf_first_page_image(path: Path, zoom: float = 2.0):
    """Первая страница как PIL-изображение (для OCR сканов без текста)."""
    from PIL import Image

    with pymupdf.open(path) as doc:
        if doc.needs_pass or doc.page_count == 0:
            return None
        pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def page_count(path: Path) -> int:
    path = Path(path)
    if path.suffix.lower() in IMAGE_EXTENSIONS:
        return 1
    try:
        with pymupdf.open(path) as doc:
            return doc.page_count
    except Exception:  # noqa: BLE001
        return 0


def append_file(out: pymupdf.Document, path: Path):
    """Добавляет PDF или картинку (на страницу А4 по размеру) в конец документа."""
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        with pymupdf.open(path) as src:
            out.insert_pdf(src)
        return
    from .ocr.ocr_engine import load_image
    import io

    img = load_image(path)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    a4 = pymupdf.paper_rect("a4")
    if img.width > img.height:
        a4 = pymupdf.Rect(0, 0, a4.height, a4.width)
    page = out.new_page(width=a4.width, height=a4.height)
    margin = 28
    page.insert_image(pymupdf.Rect(margin, margin, a4.width - margin, a4.height - margin),
                      stream=buf.getvalue(), keep_proportion=True)


def merge(files: List[Path], out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    for f in files:
        append_file(doc, f)
    tmp = out_path.with_suffix(".tmp.pdf")
    doc.save(tmp, garbage=3, deflate=True)
    doc.close()
    tmp.replace(out_path)
    return out_path
