"""Бизнес-логика: приемка архивов, догрузка документов, Master-PDF, экспорт, пакет печати.

Функции не зависят от интерфейса и вызываются из фоновых потоков.
"""

import fnmatch
import logging
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
from .ocr.front import normalize_name, parse_card, respace_with_hint
from .ocr.ocr_engine import IMAGE_EXTENSIONS, OcrEngine

Progress = Callable[[str], None]
log = logging.getLogger("e9pay")

ORIGINALS, SCANS, LOCKED = "originals", "scans", "_locked"
SCAN_MANUAL = "✓ обработан в редакторе скана"
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
    # Повтор: клиент с тем же ARC добавлялся меньше года назад — ждет решения пользователя.
    period: str = ""
    fields: dict = field(default_factory=dict)
    incoming: Optional[Path] = None
    duplicates: List[Client] = field(default_factory=list)
    old_duplicates: List[Client] = field(default_factory=list)   # больше года назад — не мешают

    @property
    def pending(self) -> bool:
        return self.client_id is None and bool(self.duplicates) and not self.error


DUPLICATE_DAYS = 365


def recent_duplicates(db: Database, arc: str, exclude_id: Optional[int] = None,
                      days: int = DUPLICATE_DAYS) -> Tuple[List[Client], List[Client]]:
    """Клиенты с тем же ARC: (добавлены меньше `days` дней назад, добавлены раньше)."""
    from datetime import datetime, timedelta, timezone
    # created_at в SQLite хранится в UTC
    border = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    recent, old = [], []
    for c in db.clients_by_arc(arc):
        if c.id == exclude_id:
            continue
        (recent if (c.created_at or "") >= border else old).append(c)
    return recent, old


def describe_client(c: Client) -> str:
    return (f"{c.full_name or 'без имени'} · ARC {c.arc_number} · добавлен {(c.created_at or '')[:10]} "
            f"· период {c.batch_period}")


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

    fields, warnings = _extract_fields(engine, result.photos, progress, Path(archive_path).stem)
    if any(p.doc_type == "" for p in result.photos):
        warnings.append("есть нераспознанные файлы")

    result.period, result.fields, result.warnings, result.incoming = period, fields, warnings, incoming
    result.duplicates, result.old_duplicates = recent_duplicates(db, fields.get("arc_number", ""))
    if result.duplicates:
        progress("Повтор: клиент уже добавлялся")
        return result  # ждем решения: удалить прежнего и принять или не принимать
    return finalize_intake(db, result, progress)


def finalize_intake(db: Database, result: IntakeResult, progress: Progress = _noop) -> IntakeResult:
    """Создает клиента из распознанного архива (после проверки повторов)."""
    period, fields, warnings = result.period, result.fields, result.warnings
    if result.old_duplicates and not any(w.startswith("ранее добавлялся") for w in warnings):
        last = result.old_duplicates[0]
        warnings.append(f"ранее добавлялся {(last.created_at or '')[:10]} (больше года назад)")
    name = fields.get("full_name") or result.archive.stem
    arc6 = (fields.get("arc_number") or "")[:6]
    folder = _unique(config.period_dir(period) / config.safe_name(f"{name}_{arc6}" if arc6 else name))
    folder.mkdir(parents=True)
    client_id = db.add_client(period, folder_path=str(folder), source_archive=result.archive.name, **fields)

    (folder / ORIGINALS).mkdir(parents=True, exist_ok=True)
    for p in result.photos:
        target = _unique(folder / ORIGINALS / p.path.name)
        shutil.move(str(p.path), target)
        p.path = target
        db.add_document(client_id, p.doc_type, target, original_name=p.path.name, detected_by=p.reason)
    if result.incoming:
        shutil.rmtree(result.incoming, ignore_errors=True)

    fronts = [p for p in result.photos if p.doc_type == detect.FRONT]
    if fronts:
        progress("Готовлю ID на А4")
        make_id_a4(db, client_id, fronts[0].path)

    _refresh_status(db, client_id, warnings)
    result.client_id = client_id
    result.duplicates = []
    return result


def replace_duplicates_and_finalize(db: Database, result: IntakeResult, progress: Progress = _noop) -> IntakeResult:
    """«Удалить прежнего и принять заново»."""
    for c in result.duplicates:
        delete_client_everything(db, c.id)
    result.duplicates = []
    return finalize_intake(db, result, progress)


def discard_intake(result: IntakeResult):
    """«Не принимать»: распознанные файлы удаляются, архив не трогается."""
    if result.incoming:
        shutil.rmtree(result.incoming, ignore_errors=True)
    result.duplicates = []


def _read_front(engine: OcrEngine, path: Path, lines: Optional[List[str]] = None):
    """(CardData, строки) — лучший из разбора готовых строк и прохода по выровненной карте."""
    card = parse_card(lines) if lines else None
    card2, lines2 = engine.read_front(path)
    if card is None or card2.filled_count() >= card.filled_count():
        return card2, lines2
    return card, lines


def _read_back(engine: OcrEngine, path: Path, lines: Optional[List[str]] = None):
    from .ocr.ocr_engine import crop_card, load_image
    card = crop_card(load_image(path))
    addr, w = back_ocr.parse_back(engine.lines_best_rotation(card))
    if not addr and max(card.size) < 1600:  # маленькое фото — вторая попытка в увеличенном виде
        addr, w = back_ocr.parse_back(engine.lines_best_rotation(card, upscale=True))
    if not addr and lines:
        addr, w = back_ocr.parse_back(lines)
    return addr, w


def _read_bank(engine: OcrEngine, path: Path, lines: Optional[List[str]] = None):
    return bank_ocr.parse_bank(lines if lines is not None else engine.read_image_lines(path))


def _extract_fields(engine: OcrEngine, photos: List[PhotoResult], progress: Progress = _noop,
                    name_hint: str = ""):
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
        fields.update(full_name=respace_with_hint(card.name, name_hint), arc_number=card.reg_no,
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
    client = db.client(client_id)
    hint = Path(client.source_archive).stem if client and client.source_archive else ""
    fields, warnings = _extract_fields(engine, photos, progress, hint)
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
    if "full_name" in fields:
        fields["full_name"] = normalize_name(fields["full_name"].upper())
    if fields.get("phone"):
        fields["phone"] = normalize_phone(fields["phone"])
    if fields.get("email"):
        fields["email"] = fields["email"].strip().lower()
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
               rotate: int = 0, manual: bool = False) -> Path:
    client = db.client(client_id)
    out_dir = client.folder / SCANS
    png, pdf = scan.process_id_photo(Path(front_path), out_dir, corners, filter_mode, strength, margin_mm,
                                     rotate=rotate)
    for d in db.documents_of_type(client_id, BUILTIN_A4):
        db.delete_document(d.id)
    db.add_document(client_id, BUILTIN_A4, pdf, original_name=pdf.name, is_processed_scan=1,
                    detected_by=SCAN_MANUAL if manual else "создан автоматически из лица ID")
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
                    detected_by=SCAN_MANUAL)
    return png


# ====================================================================== догрузка документов

NAME_TOKENS = ("{ФИО}", "{фио}", "{FIO}", "{fio}")


def name_matches_client(stem: str, client_name: str) -> bool:
    """Имя файла — это ФИО клиента (любой регистр, порядок слов, лишнее « (2)»): ABILOV BOBORAKHIM.pdf."""
    file_words = re.findall(r"[a-z]+", stem.lower())
    name_words = set(re.findall(r"[a-z]+", (client_name or "").lower()))
    return len(file_words) >= 2 and len(name_words) >= 2 and set(file_words) <= name_words


def match_pattern(pat: str, stem: str, names, client_name: str = "") -> bool:
    """Маска имени файла. «re:…» — регулярное выражение для имени без расширения (целиком),
    «{ФИО}» — имя файла совпадает с ФИО клиента, иначе маска с * и ? (без звездочек — «содержит»)."""
    pat_s = pat.strip()
    if not pat_s:
        return False
    if pat_s in NAME_TOKENS:
        return name_matches_client(stem, client_name)
    if pat_s.lower().startswith("re:"):
        try:
            return re.fullmatch(pat_s[3:].strip(), stem.strip(), re.I) is not None
        except re.error:
            return False
    pat_l = pat_s.lower()
    if "*" not in pat_l and "?" not in pat_l:
        pat_l = f"*{pat_l}*"  # маска без звездочек — «содержит»
    return any(fnmatch.fnmatch(n, pat_l) for n in names)


def classify_file(path: Path, rules: List[DocRule], engine: Optional[OcrEngine] = None,
                  original_name: str = "", client_name: str = "") -> Tuple[str, str]:
    """Тип документа по маске имени, затем по словам OCR/текста PDF. ('', причина) — не определен."""
    path = Path(path)
    candidates = [r for r in rules if r.code != BUILTIN_A4]
    names = {path.name.lower(), path.stem.lower()}
    stems = [path.stem]
    if original_name:
        names |= {original_name.lower(), Path(original_name).stem.lower()}
        stems.insert(0, Path(original_name).stem)
    for r in candidates:
        for pat in r.filename_patterns:
            if any(match_pattern(pat, st, names, client_name) for st in stems):
                return r.code, f"маска имени «{pat}»"
    text = ""
    kind = pdf_tools.real_extension(path)
    if kind == ".pdf":
        text = pdf_tools.pdf_text(path)
        if len(text.strip()) < 20 and engine is not None:
            img = pdf_tools.pdf_first_page_image(path)
            if img is not None:
                text = "\n".join(engine.lines(img))
    elif kind in IMAGE_EXTENSIONS and engine is not None:
        text = "\n".join(engine.read_image_lines(path))
    compact = re.sub(r"\s+", "", text).lower()
    for r in candidates:
        for kw in r.ocr_keywords:
            if kw and re.sub(r"\s+", "", kw).lower() in compact:
                return r.code, f"OCR: «{kw}»"
    return "", "нет совпадений"


def _named_with_real_ext(folder: Path, original: Path, real_ext: str) -> Path:
    """Имя копии в папке клиента с правильным расширением (файлы с сайтов бывают без него)."""
    base = original.stem if original.suffix.lower() in pdf_tools.KNOWN_EXTENSIONS else original.name
    return _unique(folder / (config.safe_name(base, "file") + (real_ext or original.suffix.lower())))


def _fix_extension(db: Database, doc) -> Path:
    """Документ без расширения (добавлен раньше) — переименовать по содержимому."""
    real = pdf_tools.sniff_extension(doc.path)
    if real and doc.path.suffix.lower() != real and doc.path.exists():
        target = _named_with_real_ext(doc.path.parent, doc.path, real)
        doc.path.rename(target)
        db.update_document(doc.id, file_path=target)
        doc.file_path = str(target)
    return doc.path


def _try_unlock_doc(db: Database, client: Client, doc, progress: Progress = _noop) -> bool:
    """Пароль: дата рождения и варианты ARC. True — файл открыт (или пароля не было)."""
    path = doc.path
    if pdf_tools.real_extension(path) != ".pdf" or not pdf_tools.is_encrypted(path):
        if doc.password_status == "locked":
            db.update_document(doc.id, password_status="unlocked")
        return True
    locked = client.folder / LOCKED / path.name
    if not locked.exists():
        locked.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, locked)
    candidates = pdf_tools.password_candidates(client.birth_date, client.arc_number)
    ok, pw = pdf_tools.try_unlock(path, path, candidates)
    # сами пароли (дата рождения) в журнал не пишем
    log.info("пароль %s: %s (вариантов %d%s)", path.name, "снят" if ok else "НЕ подошел", len(candidates),
             f", подошел №{candidates.index(pw) + 1}" if ok and pw in candidates else "")
    db.update_document(doc.id, password_status="unlocked" if ok else "locked")
    doc.password_status = "unlocked" if ok else "locked"
    if ok:
        progress(f"Пароль снят: {pw}")
    return ok


def _classify_doc(db: Database, doc, rules, engine: Optional[OcrEngine], progress: Progress = _noop) -> str:
    progress(f"Определяю тип: {doc.path.name}")
    client = db.client(doc.client_id)
    code, reason = classify_file(doc.path, rules, engine if doc.password_status != "locked" else None,
                                 doc.original_name, client.full_name if client else "")
    if doc.password_status == "unlocked":
        reason += " · пароль снят"
    elif doc.password_status == "locked":
        reason += " · нужен пароль"
    log.info("тип %s (исходно %r): %s — %s", doc.path.name, doc.original_name, code or "не определен", reason)
    db.update_document(doc.id, doc_type=code, detected_by=reason)
    doc.doc_type = code
    return code


def add_documents(db: Database, client_id: int, paths: Iterable[Path], engine: Optional[OcrEngine] = None,
                  progress: Progress = _noop) -> List[int]:
    """Копирует файлы в папку клиента, снимает пароли с PDF, определяет тип."""
    client = db.client(client_id)
    rules = db.rules()
    engine = engine or OcrEngine.get()
    paths = [Path(p) for p in paths]
    ids = []
    for i, path in enumerate(paths, start=1):
        progress(f"{i}/{len(paths)} · копирую {path.name}")
        real = pdf_tools.real_extension(path)
        log.info("добавляю %r: по содержимому %s, по имени %r", path.name, real or "?", path.suffix)
        dst = _named_with_real_ext(client.folder / ORIGINALS, path, real)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dst)
        doc_id = db.add_document(client_id, "", dst, original_name=path.name)
        doc = next(d for d in db.documents(client_id) if d.id == doc_id)
        progress(f"{i}/{len(paths)} · проверяю пароль {dst.name}")
        _try_unlock_doc(db, client, doc, progress)
        _classify_doc(db, doc, rules, engine, lambda m, i=i: progress(f"{i}/{len(paths)} · {m}"))
        ids.append(doc_id)
    _refresh_status(db, client_id)
    return ids


def recheck_documents(db: Database, client_id: int, engine: Optional[OcrEngine] = None,
                      progress: Progress = _noop, use_ocr: bool = True) -> int:
    """Заново: расширение по содержимому, пароли, тип для нераспознанных. Возвращает число определенных."""
    client = db.client(client_id)
    rules = db.rules()
    if use_ocr:
        engine = engine or OcrEngine.get()
    found = 0
    for doc in db.documents(client_id):
        if doc.is_processed_scan or doc.doc_type == BUILTIN_A4 or not doc.path.exists():
            continue
        _fix_extension(db, doc)
        if doc.password_status == "locked" or pdf_tools.real_extension(doc.path) == ".pdf":
            _try_unlock_doc(db, client, doc, progress)
        if not doc.doc_type:
            if _classify_doc(db, doc, rules, engine if use_ocr else None, progress):
                found += 1
    _refresh_status(db, client_id)
    return found


def recheck_all_unknown(db: Database) -> int:
    """После изменения типов: быстро (только маски имени) переопределить нераспознанные во всех клиентах."""
    found = 0
    with db.conn() as c:
        ids = [r[0] for r in c.execute(
            "SELECT DISTINCT client_id FROM client_documents WHERE doc_type='' AND is_processed_scan=0")]
    for cid in ids:
        found += recheck_documents(db, cid, use_ocr=False)
    return found


def delete_client_everything(db: Database, client_id: int):
    """Удаляет клиента из базы и его папку со всеми файлами."""
    client = db.client(client_id)
    if not client:
        return
    folder = client.folder
    db.delete_client(client_id)
    root = (config.data_dir() / "periods").resolve()
    try:
        if folder.exists() and root in folder.resolve().parents:
            shutil.rmtree(folder)
    except OSError:
        pass


def unlock_with_password(db: Database, doc_id: int, client_id: int, password: str) -> bool:
    doc = next((d for d in db.documents(client_id) if d.id == doc_id), None)
    if not doc:
        return False
    ok, _ = pdf_tools.try_unlock(doc.path, doc.path, [password])
    if ok:
        detected = doc.detected_by
        if not doc.doc_type:
            client = db.client(client_id)
            code, reason = classify_file(doc.path, db.rules(), None, doc.original_name,
                                         client.full_name if client else "")
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
    locked: List[str] = field(default_factory=list)    # PDF, к которым пароль не подошел
    unknown: List[str] = field(default_factory=list)   # документы без типа (не попали в Master-PDF)


def _natural_key(name: str):
    """21, 21-1, 21-2, 22 … 26 и 1, 2 … 10 — по порядку чисел, а не по алфавиту."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def master_queue(db: Database, client_id: int):
    """[(правило, [документы])] по очереди правил Master-PDF."""
    docs = db.documents(client_id)
    out = []
    for r in db.rules():
        if not r.in_master:
            continue
        items = sorted((d for d in docs if d.doc_type == r.code and d.password_status != "locked"),
                       key=lambda d: _natural_key(Path(d.original_name or d.path.name).stem))
        out.append((r, items))
    return out


def build_master(db: Database, client_id: int, progress: Progress = _noop) -> MasterResult:
    client = db.client(client_id)
    # Перед сборкой: исправить расширения и еще раз попробовать пароли (дата рождения могла измениться).
    for doc in db.documents(client_id):
        if doc.is_processed_scan or not doc.path.exists():
            continue
        _fix_extension(db, doc)
        if doc.password_status == "locked" or (doc.doc_type and pdf_tools.real_extension(doc.path) == ".pdf"):
            _try_unlock_doc(db, client, doc, progress)
    _refresh_status(db, client_id)
    files, included, skipped = [], [], []
    for rule, docs in master_queue(db, client_id):
        if docs:
            files += [d.path for d in docs if d.path.exists()]
            included.append(rule.doc_name)
        else:
            skipped.append(rule.doc_name)
    all_docs = db.documents(client_id)
    locked = [d.path.name for d in all_docs if d.password_status == "locked"]
    unknown = [d.path.name for d in all_docs if not d.doc_type and not d.is_processed_scan]
    if not files:
        return MasterResult(None, included, skipped, locked, unknown)
    out = client.folder / f"MASTER_{config.safe_name(client.full_name or 'client')}_{client.batch_period}.pdf"
    progress("Склеиваю PDF")
    pdf_tools.merge(files, out)
    db.update_client(client_id, master_pdf=str(out))
    return MasterResult(out, included, skipped, locked, unknown)


# ====================================================================== экспорт

REGISTRY_COLUMNS = [("№", 6), ("ФИО (заглавными)", 30), ("ФИО (строчными)", 30), ("Номер ARC", 18),
                    ("Дата рождения", 14), ("Гражданство", 16), ("Адрес регистрации", 60),
                    ("Телефон", 16), ("E-mail", 28)]


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
        name = normalize_name(c.full_name)
        ws.append([n, name.upper(), name.lower(), c.arc_number, c.birth_date, c.nationality, c.registered_address,
                   c.phone, c.email])
        for cell in ws[ws.max_row][1:]:
            cell.number_format = "@"
    ws.freeze_panes = "A2"
    out_path = Path(out_path)
    wb.save(out_path)
    return out_path


_PHONE = re.compile(r"(?<!\d)(01[016789])[-.\s]?(\d{3,4})[-.\s]?(\d{4})(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_ARC = re.compile(r"(?<!\d)(\d{6})\s*-?\s*([5-8]\d{6})(?!\d)")


def normalize_phone(text: str) -> str:
    m = _PHONE.search(text or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else (text or "").strip()


@dataclass
class RegistryImportResult:
    updated: List[str] = field(default_factory=list)       # кому что записали
    unmatched: List[str] = field(default_factory=list)     # строки, для которых клиент не найден
    rows: int = 0
    addresses_changed: int = 0


_HEADER_KEYS = {
    "arc": ("номер arc", "arc", "외국인등록번호", "등록번호"),
    "address": ("адрес", "주소", "address"),
    "phone": ("телефон", "전화", "phone", "휴대폰"),
    "email": ("e-mail", "email", "почта", "이메일", "메일"),
    "name": ("фио (заглавными)", "фио", "이름", "성명", "name"),
}


def _header_map(cells: List[str]) -> dict:
    """Номера колонок по заголовкам реестра (если это строка заголовков)."""
    low = [c.strip().lower() for c in cells]
    found = {}
    for key, names in _HEADER_KEYS.items():
        for i, c in enumerate(low):
            if any(c == n or c.startswith(n) for n in names) and i not in found.values():
                found.setdefault(key, i)
                break
    return found if len(found) >= 2 and ("arc" in found or "name" in found) else {}


def import_registry_contacts(db: Database, path: Path, period: Optional[str] = None) -> RegistryImportResult:
    """Excel реестра -> в профили клиентов: телефон, e-mail и измененный адрес.

    Если есть строка заголовков (как в экспортированном реестре) — значения берутся из колонок
    «Номер ARC», «Адрес регистрации», «Телефон», «E-mail». Без заголовков в каждой строке ищутся
    ARC (иначе ФИО), телефон 010-… и e-mail — даже если все в одной ячейке.
    Клиент ищется по ARC во всех периодах (свежий приоритетнее), без ARC — по ФИО в периоде.
    """
    from openpyxl import load_workbook

    res = RegistryImportResult()
    wb = load_workbook(Path(path), read_only=True, data_only=True)
    clients_period = db.clients(period) if period else []
    by_name = {normalize_name(c.full_name).lower(): c for c in clients_period if c.full_name}
    for ws in wb.worksheets:
        header = {}
        for row in ws.iter_rows(values_only=True):
            raw = ["" if v is None else str(v).strip() for v in row]
            cells = [c for c in raw if c]
            if not cells:
                continue
            if not header:
                header = _header_map(raw)
                if header:
                    continue  # это строка заголовков
            col = (lambda key: raw[header[key]] if key in header and header[key] < len(raw) else "")
            text = " | ".join(cells)
            # колонка пустая — ищем во всей строке (все могло быть записано в одной ячейке)
            phone_src = col("phone") or text
            email_src = col("email") or text
            phone_m, email_m = _PHONE.search(phone_src or ""), _EMAIL.search(email_src or "")
            address = re.sub(r"\s+", " ", col("address")).strip() if "address" in header else ""
            arc_src = col("arc") or _PHONE.sub(" ", text)
            arc_m = _ARC.search(arc_src or "")
            if not (phone_m or email_m or address):
                continue
            res.rows += 1
            client = None
            if arc_m:
                found = db.clients_by_arc(arc_m.group(1) + arc_m.group(2))
                client = found[0] if found else None
            if client is None:
                # ФИО может стоять в любой ячейке или вместе с телефоном/почтой
                low = " " + re.sub(r"\s+", " ", text.lower()) + " "
                hits = [(len(n), c) for n, c in by_name.items() if f" {n} " in low or f"|{n}" in low.replace(" | ", "|")]
                if hits:
                    client = max(hits, key=lambda h: h[0])[1]
            named = [c for c in cells if re.search(r"[A-Za-z가-힣А-Яа-я]", c) and "@" not in c]
            label = arc_m.group(0) if arc_m else (named[0] if named else cells[0])
            if client is None:
                res.unmatched.append(label)
                continue
            updates, notes = {}, []
            if phone_m:
                updates["phone"] = f"{phone_m.group(1)}-{phone_m.group(2)}-{phone_m.group(3)}"
                notes.append(updates["phone"])
            if email_m:
                updates["email"] = email_m.group(0).lower()
                notes.append(updates["email"])
            if address and address != re.sub(r"\s+", " ", client.registered_address or "").strip():
                updates["registered_address"] = address
                res.addresses_changed += 1
                notes.append(f"новый адрес: {address}")
            if updates:
                db.update_client(client.id, **updates)
                if "registered_address" in updates:
                    set_client_fields(db, client.id)  # снять предупреждение «адрес не найден»
                res.updated.append(f"{client.full_name}: {', '.join(notes)}")
    wb.close()
    return res


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
