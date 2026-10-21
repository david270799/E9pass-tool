"""Учебная база OCR: размеченные фото карт для словаря имен и дообучения моделей.

Все хранится только на этом компьютере: папка training в папке данных E9pass
(фото карт и training.sqlite3 с правильными ответами). Клиенты при разметке не создаются.
"""

import hashlib
import re
import shutil
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Set

from . import config

FRONT, BACK, OTHER = "front", "back", "other"
# статус карты: new — ждет OCR, ready — OCR прочитал, нужно проверить, done — проверено, skip — пропущено
NEW, READY, DONE, SKIP = "new", "ready", "done", "skip"

SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    image TEXT NOT NULL,
    image_hash TEXT UNIQUE,
    original_name TEXT DEFAULT '',
    side TEXT DEFAULT '',
    status TEXT DEFAULT 'new',
    name TEXT DEFAULT '', arc TEXT DEFAULT '', country TEXT DEFAULT '', address TEXT DEFAULT '',
    pred_name TEXT DEFAULT '', pred_arc TEXT DEFAULT '', pred_country TEXT DEFAULT '', pred_address TEXT DEFAULT '',
    source TEXT DEFAULT 'photo',
    added_at TEXT DEFAULT CURRENT_TIMESTAMP,
    checked_at TEXT DEFAULT ''
);
"""

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp", ".heic"}


def training_dir() -> Path:
    path = config.data_dir() / "training"
    (path / "images").mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Card:
    id: int
    image: str
    image_hash: str
    original_name: str
    side: str
    status: str
    name: str
    arc: str
    country: str
    address: str
    pred_name: str
    pred_arc: str
    pred_country: str
    pred_address: str
    source: str
    added_at: str
    checked_at: str

    @property
    def path(self) -> Path:
        return Path(self.image)


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class TrainingStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else training_dir() / "training.sqlite3"
        (self.path.parent / "images").mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    # ------------------------------------------------------------ карты

    def add_image(self, src: Path, side: str = "", status: str = NEW, source: str = "photo",
                  **labels) -> Optional[int]:
        """Копирует фото в учебную базу. Повтор того же файла не добавляется (None)."""
        src = Path(src)
        digest = _sha1(src)
        with self.conn() as c:
            if c.execute("SELECT 1 FROM cards WHERE image_hash=?", (digest,)).fetchone():
                return None
        dst = self.path.parent / "images" / f"{digest[:16]}{src.suffix.lower()}"
        if not dst.exists():
            shutil.copy2(src, dst)
        fields = {k: v for k, v in labels.items() if k in ("name", "arc", "country", "address")}
        cols = ["image", "image_hash", "original_name", "side", "status", "source"] + list(fields)
        vals = [str(dst), digest, src.name, side, status, source] + list(fields.values())
        if status == DONE:
            cols.append("checked_at")
            vals.append(datetime.now().isoformat(timespec="seconds"))
        with self.conn() as c:
            cur = c.execute(f"INSERT INTO cards({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)
            return cur.lastrowid

    def add_images(self, paths: Iterable[Path]) -> List[int]:
        ids = []
        for p in paths:
            if Path(p).suffix.lower() in IMAGE_EXTENSIONS:
                cid = self.add_image(Path(p))
                if cid:
                    ids.append(cid)
        return ids

    def cards(self, status: Optional[str] = None) -> List[Card]:
        q, args = "SELECT * FROM cards", ()
        if status:
            q, args = q + " WHERE status=?", (status,)
        with self.conn() as c:
            return [Card(**dict(r)) for r in c.execute(q + " ORDER BY id", args)]

    def card(self, card_id: int) -> Optional[Card]:
        with self.conn() as c:
            r = c.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
            return Card(**dict(r)) if r else None

    def set_prediction(self, card_id: int, side: str, name="", arc="", country="", address=""):
        """Результат OCR: подставляется и как черновик ответа — человеку остается проверить."""
        with self.conn() as c:
            c.execute("UPDATE cards SET side=?, status=?, pred_name=?, pred_arc=?, pred_country=?, pred_address=?,"
                      " name=?, arc=?, country=?, address=? WHERE id=? AND status=?",
                      (side, READY, name, arc, country, address, name, arc, country, address, card_id, NEW))

    def save_label(self, card_id: int, side: str, name="", arc="", country="", address=""):
        with self.conn() as c:
            c.execute("UPDATE cards SET side=?, status=?, name=?, arc=?, country=?, address=?, checked_at=?"
                      " WHERE id=?", (side, DONE if side in (FRONT, BACK) else SKIP, name.strip(), arc.strip(),
                                      country.strip(), " ".join(address.split()),
                                      datetime.now().isoformat(timespec="seconds"), card_id))

    def skip(self, card_id: int):
        with self.conn() as c:
            c.execute("UPDATE cards SET status=? WHERE id=?", (SKIP, card_id))

    def delete(self, card_id: int):
        card = self.card(card_id)
        with self.conn() as c:
            c.execute("DELETE FROM cards WHERE id=?", (card_id,))
        if card and card.path.exists() and card.path.parent == self.path.parent / "images":
            card.path.unlink(missing_ok=True)

    def stats(self) -> dict:
        with self.conn() as c:
            rows = c.execute("SELECT status, side, COUNT(*) n FROM cards GROUP BY status, side").fetchall()
        st = {"total": 0, NEW: 0, READY: 0, DONE: 0, SKIP: 0, "front": 0, "back": 0}
        for r in rows:
            st["total"] += r["n"]
            st[r["status"]] = st.get(r["status"], 0) + r["n"]
            if r["status"] == DONE and r["side"] in (FRONT, BACK):
                st[r["side"]] += r["n"]
        return st

    # ------------------------------------------------------------ из клиентов программы

    def import_clients(self, db) -> int:
        """Клиенты программы — уже проверенные данные: фото лица с ФИО/номером/страной и фото оборота с адресом."""
        from .db import BUILTIN_BACK, BUILTIN_FRONT

        added = 0
        for client in db.all_clients():
            for d in db.documents(client.id):
                if d.is_processed_scan or not d.path.exists():
                    continue
                if d.doc_type == BUILTIN_FRONT and client.full_name:
                    cid = self.add_image(d.path, FRONT, DONE, "client", name=client.full_name,
                                         arc=client.arc_number, country=client.nationality)
                elif d.doc_type == BUILTIN_BACK and client.registered_address:
                    cid = self.add_image(d.path, BACK, DONE, "client", address=client.registered_address)
                else:
                    continue
                added += bool(cid)
        return added


# ------------------------------------------------------------ предварительный OCR

def predict(engine, path: Path):
    """(сторона, поля) — что OCR видит на фото: лицо (ФИО, номер, страна) или оборот (адрес)."""
    from . import services
    from .ocr import detect

    kind, _reason = detect.classify_photo(engine.read_image_lines(path))
    if kind == detect.FRONT:
        card, _lines = engine.read_front(path)
        return FRONT, {"name": card.name, "arc": card.reg_no, "country": card.country}
    if kind == detect.BACK:
        addr, _w = services._read_back(engine, Path(path))
        return BACK, {"address": addr}
    # Тип не определился (фото повернуто, пестрый фон) — пробуем прочитать как лицо, затем как оборот.
    card, _lines = engine.read_front(path)
    if card.reg_no or (card.name and card.country):
        return FRONT, {"name": card.name, "arc": card.reg_no, "country": card.country}
    addr, _w = services._read_back(engine, Path(path))
    if addr:
        return BACK, {"address": addr}
    return OTHER, {}


def predict_pending(store: TrainingStore, engine=None, progress: Callable[[str], None] = lambda _m: None) -> int:
    """Прогоняет OCR по всем новым картам. Возвращает, сколько обработано."""
    from .ocr.ocr_engine import OcrEngine

    engine = engine or OcrEngine.get()
    todo = store.cards(NEW)
    for i, card in enumerate(todo, 1):
        progress(f"OCR {i}/{len(todo)}: {card.original_name}")
        try:
            side, fields = predict(engine, card.path)
        except Exception:  # noqa: BLE001 — битое фото не должно останавливать всю очередь
            side, fields = OTHER, {}
        store.set_prediction(card.id, side, **fields)
    return len(todo)


# ------------------------------------------------------------ словарь имен

def _levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def name_words(db=None, store: Optional[TrainingStore] = None) -> Set[str]:
    """Слова ФИО из проверенной учебной базы и из клиентов программы."""
    words: Set[str] = set()
    names = []
    try:
        store = store or TrainingStore()
        names += [c.name for c in store.cards(DONE) if c.side == FRONT and c.name]
    except sqlite3.Error:
        pass
    if db is not None:
        names += [c.full_name for c in db.all_clients() if c.full_name]
    for n in names:
        words |= {w for w in re.findall(r"[A-Z][A-Z'\-]+", n.upper()) if len(w) >= 3}
    return words


def correct_with_dictionary(name: str, words: Set[str]) -> str:
    """Слово ФИО, которого нет в словаре, но которое отличается от известного на 1 букву
    (на 2 — у длинных слов), заменяется известным: IBRATBFK -> IBRATBEK. Только если вариант один."""
    if not name or not words:
        return name
    out = []
    for w in name.split():
        if w in words or len(w) < 6:
            out.append(w)
            continue
        limit = 1 if len(w) < 10 else 2
        close = [k for k in words if abs(len(k) - len(w)) <= limit and _levenshtein(w, k) <= limit]
        out.append(close[0] if len(close) == 1 else w)
    return " ".join(out)
