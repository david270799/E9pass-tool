"""Бизнес-логика: приемка архивов, догрузка документов, Master-PDF, экспорт, пакет печати.

Функции не зависят от интерфейса и вызываются из фоновых потоков.
"""

import fnmatch
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import archive, config, pdf_tools, scan
from .db import BUILTIN_A4, BUILTIN_BACK, BUILTIN_BANK, BUILTIN_FRONT, Client, Database, DocRule
from .ocr import back as back_ocr
from .ocr import bank as bank_ocr
from .ocr import detect
from .ocr.front import parse_card
from .ocr.ocr_engine import IMAGE_EXTENSIONS, OcrEngine

Progress = Callable[[str], None]

ORIGINALS, SCANS, LOCKED = "originals", "scans", "_locked"
_FRONT_WARN_KEEP = ("номер", "ФИО", "страна", "пол")


def _noop(_msg: str):
    pass


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 10000):
        cand = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not cand.exists():
            return cand
    raise RuntimeError(path)


# ====================================================================== приемка

@dataclass
class PhotoResult:
    path: Path
    doc_type: str
    reason: str
    lines: List[str] = field(default_factory=list)


@dataclass
class IntakeResult:
    archive: Path
    client_id: Optional[int] = None
    merged_into_existing: bool = False
    photos: List[PhotoResult] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    error: str = ""


def intake_archive(db: Database, period: str, archive_path: Path, engine: Optional[OcrEngine] = None,
                   progress: Progress = _noop) -> IntakeResult:
    """Архив клиента (лицо ID, оборот ID, 통장) -> клиент в базе и папка на диске."""
    archive_path = Path(archive_path)
    result = IntakeResult(archive=archive_path)
    incoming = config.period_dir(period) / "_incoming" / config.safe_name(archive_path.stem)
    if incoming.exists():
        shutil.rmtree(incoming)
    try:
        progress("Распаковка")
        files = archive.extract(archive_path, incoming)
    except archive.ArchiveError as e:
        result.error = str(e)
        return result
    if not files:
        result.error = "архив пустой"
        return result

    engine = engine or OcrEngine.get()
    images = [f for f in files if f.suffix.lower() in IMAGE_EXTENSIONS]
    for i, img in enumerate(images, start=1):
        progress(f"OCR {i}/{len(images)}: {img.name}")
        lines = engine.read_image_lines(img)
        doc_type, reason = detect.classify_photo(lines)
        result.photos.append(PhotoResult(img, doc_type, reason, lines))
    for f in files:
        if f.suffix.lower() not in IMAGE_EXTENSIONS:
            result.photos.append(PhotoResult(f, "", "не фото"))

    fields, warnings = _extract_fields(engine, result.photos, progress)
    if any(p.doc_type == "" for p in result.photos):
        warnings.append("есть нераспознанные файлы")

    # --- клиент: новый или уже есть в периоде с тем же ARC
    existing = db.find_by_arc(period, fields.get("arc_number", ""))
    if existing:
        client_id = existing.id
        folder = existing.folder
        result.merged_into_existing = True
        updates = {k: v for k, v in fields.items() if v and not getattr(existing, k)}
        db.update_client(client_id, **updates)
    else:
        name = fields.get("full_name") or archive_path.stem
        arc6 = (fields.get("arc_number") or "")[:6]
        folder = _unique(config.period_dir(period) / config.safe_name(f"{name}_{arc6}" if arc6 else name))
        folder.mkdir(parents=True)
        client_id = db.add_client(period, folder_path=str(folder), source_archive=archive_path.name, **fields)

    (folder / ORIGINALS).mkdir(parents=True, exist_ok=True)
    for p in result.photos:
        target = _unique(folder / ORIGINALS / p.path.name)
        shutil.move(str(p.path), target)
        p.path = target
        db.add_document(client_id, p.doc_type, target, original_name=p.path.name, detected_by=p.reason)
    shutil.rmtree(incoming, ignore_errors=True)

    # --- ID на А4 из лицевой стороны
    fronts = [p for p in result.photos if p.doc_type == detect.FRONT]
    if fronts:
        progress("Готовлю ID на А4")
        make_id_a4(db, client_id, fronts[0].path)

    _refresh_status(db, client_id, warnings)
    result.client_id = client_id
    result.warnings = warnings
    return result


def _read_front(engine: OcrEngine, path: Path, lines: Optional[List[str]] = None):
    """(CardData, строки) — лучший из разбора готовых строк и прохода по выровненной карте."""
    card = parse_card(lines) if lines else None
    card2, lines2 = engine.read_front(path)
    if card is None or card2.filled_count() >= card.filled_count():
        return card2, lines2
    return card, lines


def _read_back(engine: OcrEngine, path: Path, lines: Optional[List[str]] = None):
    from .ocr.ocr_engine import crop_card, load_image
    lines2 = engine.lines_best_rotation(crop_card(load_image(path)))
    addr, w = back_ocr.parse_back(lines2)
    if not addr and lines:
        addr, w = back_ocr.parse_back(lines)
    return addr, w


def _read_bank(engine: OcrEngine, path: Path, lines: Optional[List[str]] = None):
    return bank_ocr.parse_bank(lines if lines is not None else engine.read_image_lines(path))


def _extract_fields(engine: OcrEngine, photos: List[PhotoResult], progress: Progress = _noop):
    """Поля клиента из фото по их типам: (fields, warnings)."""
    fields, warnings = {}, []
    fronts = [p for p in photos if p.doc_type == detect.FRONT]
    card = None
    if fronts:
        progress("Читаю лицевую сторону")
        card, fronts[0].lines = _read_front(engine, fronts[0].path, fronts[0].lines)
    else:  # лицо не определилось — пробуем нераспознанные фото как лицо
        for p in photos:
            if p.doc_type == "" and p.path.suffix.lower() in IMAGE_EXTENSIONS:
                card2, lines2 = engine.read_front(p.path)
                if card2.reg_no:
                    p.doc_type, p.reason, p.lines, card = detect.FRONT, "OCR: номер ARC", lines2, card2
                    break
    if card:
        fields.update(full_name=card.name, arc_number=card.reg_no,
                      birth_date=card.reg_no[:6] if card.reg_no else "", nationality=card.country)
        warnings += [w for w in card.warnings if w.startswith(_FRONT_WARN_KEEP)]
    else:
        warnings.append("нет фото лицевой стороны ID")

    backs = [p for p in photos if p.doc_type == detect.BACK]
    if backs:
        progress("Читаю оборот")
        addr, w = _read_back(engine, backs[0].path, backs[0].lines)
        fields["registered_address"] = addr
        warnings += w
    else:
        warnings.append("нет фото оборота ID")

    banks = [p for p in photos if p.doc_type == detect.BANK]
    if banks:
        progress("Читаю 통장")
        bank_name, acc, w = _read_bank(engine, banks[0].path, banks[0].lines or None)
        fields.update(bank_name=bank_name, account_number=acc)
        warnings += w
    else:
        warnings.append("нет фото 통장사본")
    return fields, warnings


def reread_client(db: Database, client_id: int, engine: Optional[OcrEngine] = None,
                  progress: Progress = _noop):
    """Заново распознать фото клиента по их текущим типам (после ручной смены типа)."""
    engine = engine or OcrEngine.get()
    docs = [d for d in db.documents(client_id) if not d.is_processed_scan]
    photos = [PhotoResult(d.path, d.doc_type, d.detected_by) for d in docs
              if d.doc_type in (BUILTIN_FRONT, BUILTIN_BACK, BUILTIN_BANK)]
    fields, warnings = _extract_fields(engine, photos, progress)
    if any(not d.doc_type for d in docs):
        warnings.append("есть нераспознанные файлы")
    db.update_client(client_id, **fields)
    fronts = [p for p in photos if p.doc_type == BUILTIN_FRONT]
    if fronts:
        make_id_a4(db, client_id, fronts[0].path)
    _refresh_status(db, client_id, warnings)
    return fields


def _refresh_status(db: Database, client_id: int, warnings: Optional[List[str]] = None):
    client = db.client(client_id)
    if warnings is None:
        warnings = client.warnings
    if any(d.password_status == "locked" for d in db.documents(client_id)):
        if "PDF с паролем" not in warnings:
            warnings = warnings + ["PDF с паролем"]
    else:
        warnings = [w for w in warnings if w != "PDF с паролем"]
    db.update_client(client_id, warnings=warnings, status="review" if warnings else "ready")


def set_client_fields(db: Database, client_id: int, **fields):
    """Ручная правка профиля: снимает предупреждения о заполненных полях."""
    db.update_client(client_id, **fields)
    client = db.client(client_id)
    resolved = {
        "full_name": ("ФИО",), "arc_number": ("номер",), "nationality": ("страна",),
        "registered_address": ("адрес",), "bank_name": ("банк",), "account_number": ("номер счета",),
    }
    warnings = list(client.warnings)
    for key, prefixes in resolved.items():
        if getattr(client, key):
            warnings = [w for w in warnings if not w.startswith(prefixes)]
    _refresh_status(db, client_id, warnings)


def make_id_a4(db: Database, client_id: int, front_path: Path, corners=None,
               filter_mode: str = scan.FILTER_CONTRAST, strength: int = 50, margin_mm: float = 10,
               rotate: int = 0) -> Path:
    client = db.client(client_id)
    out_dir = client.folder / SCANS
    png, pdf = scan.process_id_photo(Path(front_path), out_dir, corners, filter_mode, strength, margin_mm,
                                     rotate=rotate)
    for d in db.documents_of_type(client_id, BUILTIN_A4):
        db.delete_document(d.id)
    db.add_document(client_id, BUILTIN_A4, pdf, original_name=pdf.name, is_processed_scan=1,
                    detected_by="создан из лица ID")
    return pdf


def save_back_scan(db: Database, client_id: int, back_path: Path, corners=None,
                   filter_mode: str = scan.FILTER_CONTRAST, strength: int = 50, rotate: int = 0) -> Path:
    """Обработанный скан оборота (для ZIP), без листа А4."""
    client = db.client(client_id)
    png, _ = scan.process_id_photo(Path(back_path), client.folder / SCANS, corners, filter_mode, strength,
                                   stem="ID_back", rotate=rotate, make_pdf=False)
    for d in db.documents_of_type(client_id, BUILTIN_BACK):
        if d.is_processed_scan:
            db.delete_document(d.id)
    db.add_document(client_id, BUILTIN_BACK, png, original_name=png.name, is_processed_scan=1,
                    detected_by="редактор скана")
    return png


# ====================================================================== догрузка документов

def classify_file(path: Path, rules: List[DocRule], engine: Optional[OcrEngine] = None) -> Tuple[str, str]:
    """Тип документа по маске имени, затем по словам OCR/текста PDF. ('', причина) — не определен."""
    path = Path(path)
    candidates = [r for r in rules if r.code != BUILTIN_A4]
    name = path.name.lower()
    for r in candidates:
        for pat in r.filename_patterns:
            if fnmatch.fnmatch(name, pat.lower()):
                return r.code, f"маска имени «{pat}»"
    text = ""
    if path.suffix.lower() == ".pdf":
        text = pdf_tools.pdf_text(path)
        if len(text.strip()) < 20 and engine is not None:
            img = pdf_tools.pdf_first_page_image(path)
            if img is not None:
                text = "\n".join(engine.lines(img))
    elif path.suffix.lower() in IMAGE_EXTENSIONS and engine is not None:
        text = "\n".join(engine.read_image_lines(path))
    compact = re.sub(r"\s+", "", text).lower()
    for r in candidates:
        for kw in r.ocr_keywords:
            if kw and re.sub(r"\s+", "", kw).lower() in compact:
                return r.code, f"OCR: «{kw}»"
    return "", "нет совпадений"


def add_documents(db: Database, client_id: int, paths: Iterable[Path], engine: Optional[OcrEngine] = None,
                  progress: Progress = _noop) -> List[int]:
    """Копирует файлы в папку клиента, снимает пароли с PDF, определяет тип."""
    client = db.client(client_id)
    rules = db.rules()
    engine = engine or OcrEngine.get()
    passwords = pdf_tools.birth_passwords(client.birth_date, client.arc_number)
    ids = []
    for path in paths:
        path = Path(path)
        progress(f"Добавляю {path.name}")
        dst = _unique(client.folder / ORIGINALS / (config.safe_name(path.stem, "file") + path.suffix.lower()))
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dst)
        status = ""
        if dst.suffix == ".pdf" and pdf_tools.is_encrypted(dst):
            locked = _unique(client.folder / LOCKED / dst.name)
            locked.parent.mkdir(exist_ok=True)
            shutil.copy2(dst, locked)
            ok, pw = pdf_tools.try_unlock(dst, dst, passwords)
            status = "unlocked" if ok else "locked"
            if ok:
                progress(f"Пароль снят: {pw}")
        doc_type, reason = classify_file(dst, rules, engine) if status != "locked" else \
            classify_file(dst, rules, None)
        if status == "unlocked":
            reason += " · пароль снят"
        ids.append(db.add_document(client_id, doc_type, dst, original_name=path.name,
                                   password_status=status, detected_by=reason))
    _refresh_status(db, client_id)
    return ids


def unlock_with_password(db: Database, doc_id: int, client_id: int, password: str) -> bool:
    doc = next((d for d in db.documents(client_id) if d.id == doc_id), None)
    if not doc:
        return False
    ok, _ = pdf_tools.try_unlock(doc.path, doc.path, [password])
    if ok:
        detected = doc.detected_by
        if not doc.doc_type:
            code, reason = classify_file(doc.path, db.rules(), None)
            db.update_document(doc_id, doc_type=code)
            detected = reason
        db.update_document(doc_id, password_status="unlocked", detected_by=f"{detected} · пароль введен вручную")
        _refresh_status(db, client_id)
    return ok


# ====================================================================== Master-PDF

@dataclass
class MasterResult:
    path: Optional[Path]
    included: List[str]
    skipped: List[str]


def master_queue(db: Database, client_id: int):
    """[(правило, [документы])] по очереди правил Master-PDF."""
    docs = db.documents(client_id)
    out = []
    for r in db.rules():
        if not r.in_master:
            continue
        items = [d for d in docs if d.doc_type == r.code and d.password_status != "locked"]
        out.append((r, items))
    return out


def build_master(db: Database, client_id: int) -> MasterResult:
    client = db.client(client_id)
    files, included, skipped = [], [], []
    for rule, docs in master_queue(db, client_id):
        if docs:
            files += [d.path for d in docs if d.path.exists()]
            included.append(rule.doc_name)
        else:
            skipped.append(rule.doc_name)
    if not files:
        return MasterResult(None, included, skipped)
    out = client.folder / f"MASTER_{config.safe_name(client.full_name or 'client')}_{client.batch_period}.pdf"
    pdf_tools.merge(files, out)
    db.update_client(client_id, master_pdf=str(out))
    return MasterResult(out, included, skipped)


# ====================================================================== экспорт

REGISTRY_COLUMNS = [("№", 6), ("ФИО", 28), ("Номер ARC", 18), ("Дата рождения", 14),
                    ("Гражданство", 16), ("Адрес регистрации", 60)]


def export_registry(clients: List[Client], out_path: Path) -> Path:
    """Excel-реестр строго с полями ID + адрес. Банковские данные не выгружаются."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Реестр"
    ws.append([c for c, _ in REGISTRY_COLUMNS])
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="F5C518")
        cell.alignment = Alignment(vertical="center")
    for i, (_, width) in enumerate(REGISTRY_COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
    for n, c in enumerate(clients, start=1):
        ws.append([n, c.full_name, c.arc_number, c.birth_date, c.nationality, c.registered_address])
        for cell in ws[ws.max_row][1:]:
            cell.number_format = "@"
    ws.freeze_panes = "A2"
    out_path = Path(out_path)
    wb.save(out_path)
    return out_path


def export_client_zip(db: Database, client_id: int, out_dir: Path) -> Path:
    """ZIP клиента: originals/ (PDF без пароля) + scans/ + Master-PDF."""
    client = db.client(client_id)
    if not client.master_pdf or not Path(client.master_pdf).exists():
        build_master(db, client_id)
        client = db.client(client_id)
    out = _unique(Path(out_dir) / f"{config.safe_name(client.full_name or 'client')}_{client.batch_period}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for d in db.documents(client_id):
            if not d.path.exists() or d.password_status == "locked":
                continue
            folder = SCANS if d.is_processed_scan else ORIGINALS
            zf.write(d.path, f"{folder}/{d.path.name}")
        scans = client.folder / SCANS
        if scans.exists():
            names = set(zf.namelist())
            for f in sorted(scans.iterdir()):
                if f.is_file() and f"{SCANS}/{f.name}" not in names:
                    zf.write(f, f"{SCANS}/{f.name}")
        if client.master_pdf and Path(client.master_pdf).exists():
            zf.write(client.master_pdf, Path(client.master_pdf).name)
    return out


def print_plan(db: Database, client_ids: List[int]):
    """Для печати: [(клиент, [файлы по порядку], [чего не хватает])].

    Порядок по каждому клиенту: документы с отметкой «Печать» (договор и т.п.), затем ID на А4.
    """
    rules = [r for r in db.rules() if r.print_step3]
    doc_rules = [r for r in rules if r.code != BUILTIN_A4]
    plan = []
    for cid in client_ids:
        client = db.client(cid)
        docs = db.documents(cid)
        files, missing = [], []
        for r in doc_rules + [r for r in rules if r.code == BUILTIN_A4]:
            items = [d.path for d in docs if d.doc_type == r.code and d.password_status != "locked"
                     and d.path.exists()]
            if items:
                files += items
            else:
                missing.append(r.doc_name)
        plan.append((client, files, missing))
    return plan


def build_batch_print(db: Database, client_ids: List[int], out_path: Path) -> Path:
    files = []
    for _client, client_files, _missing in print_plan(db, client_ids):
        files += client_files
    return pdf_tools.merge(files, out_path)


def mark_printed(db: Database, client_ids: List[int]):
    for cid in client_ids:
        db.update_client(cid, printed=1)
