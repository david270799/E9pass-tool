"""Договоры E9PASS (서비스 신청서) → ФИО, дата рождения, телефон, e-mail → Excel.

Бланк приходит PDF с текстовым слоем: данные берутся из текста (точно и мгновенно).
Если текста нет (скан или фото бланка) — страница читается OCR."""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

CONTRACT_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}

_MOBILE = re.compile(r"(?<!\d)(01[016789])[-.\s]?(\d{3,4})[-.\s]?(\d{4})(?!\d)")
_RRN = re.compile(r"(?<!\d)(\d{6})\s*-?\s*[\d*]{1,7}")          # 930316******* / 930316-5******
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


@dataclass
class ContractData:
    file: str
    full_name: str = ""
    birth_date: str = ""      # ГГММДД, как в реестре
    phone: str = ""
    email: str = ""
    source: str = ""          # «текст PDF» / «OCR»
    error: str = ""

    @property
    def birth_dotted(self) -> str:
        """930316 → 16.03.1993 (век — по первой цифре года: 00–29 → 20xx)."""
        d = self.birth_date
        if len(d) != 6 or not d.isdigit():
            return ""
        yy = int(d[:2])
        return f"{d[4:6]}.{d[2:4]}.{2000 + yy if yy < 30 else 1900 + yy}"

    @property
    def problems(self) -> List[str]:
        if self.error:
            return [self.error]
        return [t for t, v in (("нет ФИО", self.full_name), ("нет даты рождения", self.birth_date),
                               ("нет телефона", self.phone)) if not v]


def file_hash(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _valid_birth(d: str) -> bool:
    return len(d) == 6 and d.isdigit() and 1 <= int(d[2:4]) <= 12 and 1 <= int(d[4:6]) <= 31


def _after_label(lines: List[str], labels, extract: Callable[[str], str]) -> str:
    """Значение поля бланка: в той же строке после подписи (на скане ячейки таблицы сливаются в одну строку:
    «이름 TOSHBOEV ABBOS 주민등록번호 930316…») или на следующей строке (текстовый слой PDF)."""
    for i, line in enumerate(lines):
        for lab in labels:
            pos = line.find(lab)
            if pos < 0:
                continue
            value = extract(line[pos + len(lab):])
            if value:
                return value
            for nxt in lines[i + 1:i + 3]:
                value = extract(nxt)
                if value:
                    return value
    return ""


def _latin_name(text: str) -> str:
    m = re.match(r"\s*[:：]?\s*([A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*)+)", text)
    return m.group(1) if m else ""


def _birth(text: str) -> str:
    m = _RRN.search(text)
    return m.group(1) if m and _valid_birth(m.group(1)) else ""


def _mobile(text: str) -> str:
    m = _MOBILE.search(text)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def normalize_mobile(text: str) -> str:
    """Ручной ввод «010 9999 8888» / «01099998888» → 010-9999-8888."""
    return _mobile(text)


def normalize_birth(text: str) -> str:
    """Ручной ввод: 930316, 93-03-16, 16.03.1993 → 930316."""
    digits = re.sub(r"\D", "", text)
    if len(digits) == 8 and int(digits[4:]) > 1900:      # ДДММГГГГ
        digits = digits[6:] + digits[2:4] + digits[:2]
    elif len(digits) == 8:                                # ГГГГММДД
        digits = digits[2:]
    return digits if _valid_birth(digits) else ""


def _email(text: str) -> str:
    m = _EMAIL.search(text)
    return m.group(0).lower() if m else ""


def parse_contract_text(text: str) -> dict:
    """Поля из текста бланка (текстовый слой PDF или строки OCR)."""
    lines = [" ".join(x.split()) for x in text.splitlines()]
    lines = [x for x in lines if x]
    name = _after_label(lines, ("이름", "성명"), _latin_name)
    for i, line in enumerate(lines):  # подпись «이름 : TOSHBOEV ABBOS (인)» + «KHOLBOEVICH» строкой ниже
        m = re.search(r"(?:이름|성명)\s*[:：]\s*([A-Za-z][A-Za-z' \-]*)", line)
        if m and i + 1 < len(lines) and re.fullmatch(r"[A-Za-z'\-]+", lines[i + 1]):
            tail = " ".join(m.group(1).split()) + " " + lines[i + 1]
            if len(tail.split()) > len(name.split()):  # имя из шапки разорвано на строки — берем полное
                name = tail
            break
    birth = _after_label(lines, ("주민등록번호", "외국인등록번호", "생년월일"), _birth)
    phone = _after_label(lines, ("휴대폰 번호", "휴대폰번호", "휴대폰", "핸드폰"), _mobile) or _mobile(text)
    email = _after_label(lines, ("이메일", "e-mail", "E-mail"), _email) or _email(text)
    return {"full_name": " ".join(name.split()).upper(), "birth_date": birth, "phone": phone, "email": email}


def _ocr_text(path: Path, engine=None) -> str:
    import pymupdf
    from PIL import Image

    from .ocr.ocr_engine import OcrEngine, group_lines, load_image

    engine = engine or OcrEngine.get()
    images = []
    if path.suffix.lower() == ".pdf":
        with pymupdf.open(path) as doc:
            for page in doc:
                pix = page.get_pixmap(dpi=200)
                images.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    else:
        images.append(load_image(path))
    out = []
    for img in images:
        out += group_lines(engine.readtext(img, latin=True))
    return "\n".join(out)


def read_contract(path: Path, engine=None) -> ContractData:
    path = Path(path)
    res = ContractData(file=path.name)
    try:
        text = ""
        if path.suffix.lower() == ".pdf":
            import pymupdf
            with pymupdf.open(path) as doc:
                if doc.needs_pass:
                    res.error = "PDF с паролем"
                    return res
                text = "\n".join(p.get_text() for p in doc)
        fields = parse_contract_text(text) if text.strip() else {}
        res.source = "текст PDF"
        if not fields or not (fields["full_name"] and fields["phone"]):
            fields = parse_contract_text(_ocr_text(path, engine))  # скан / фото бланка
            res.source = "OCR"
        res.full_name, res.birth_date = fields["full_name"], fields["birth_date"]
        res.phone, res.email = fields["phone"], fields["email"]
    except Exception:  # noqa: BLE001 — один битый файл не останавливает остальные
        res.error = "файл поврежден или не PDF"
    return res


def read_contracts(paths: List[Path], engine=None, progress: Callable[[str], None] = lambda _m: None,
                   ) -> List[ContractData]:
    out = []
    for i, p in enumerate(paths, 1):
        progress(f"Договоры: {i}/{len(paths)} · {Path(p).name}")
        out.append(read_contract(Path(p), engine))
    return out


# Заголовки совпадают с реестром: этот файл можно загрузить кнопкой «Импорт реестра» — телефоны и e-mail
# попадут в карточки клиентов (поиск по ФИО).
EXCEL_COLUMNS = [("№", 6), ("ФИО", 36), ("Дата рождения", 14), ("Дата рождения (ДД.ММ.ГГГГ)", 16),
                 ("Телефон", 16), ("E-mail", 30), ("Файл", 40)]


def export_contracts(rows: List[ContractData], out_path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Договоры"
    ws.append([c for c, _ in EXCEL_COLUMNS])
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="F5C518")
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for i, (_, width) in enumerate(EXCEL_COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
    for n, r in enumerate(rows, start=1):
        ws.append([n, r.full_name, r.birth_date, r.birth_dotted, r.phone, r.email, r.file])
        for cell in ws[ws.max_row][1:]:
            cell.number_format = "@"  # 010…, 930316 — как текст, без потери нулей
    ws.freeze_panes = "A2"
    out_path = Path(out_path)
    wb.save(out_path)
    return out_path
