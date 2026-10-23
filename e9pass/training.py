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
    checked_at TEXT DEFAULT '',
    rotation INTEGER DEFAULT 0,
    regions TEXT DEFAULT '{}'
);
"""
# Области полей на фото: {"name": [x0, y0, x1, y1], ...} в долях (0..1) фото, повернутого на rotation.
FIELDS_FRONT = ("name", "arc", "country")
FIELDS_BACK = ("address",)

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
    rotation: int = 0
    regions: str = "{}"

    @property
    def path(self) -> Path:
        return Path(self.image)

    @property
    def region_map(self) -> dict:
        import json
        try:
            return {k: v for k, v in json.loads(self.regions or "{}").items() if len(v) == 4}
        except ValueError:
            return {}


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
            cols = {r[1] for r in c.execute("PRAGMA table_info(cards)")}
            if "rotation" not in cols:
                c.execute("ALTER TABLE cards ADD COLUMN rotation INTEGER DEFAULT 0")
            if "regions" not in cols:
                c.execute("ALTER TABLE cards ADD COLUMN regions TEXT DEFAULT '{}'")

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

    def set_prediction(self, card_id: int, side: str, name="", arc="", country="", address="",
                       rotation: int = 0, regions: Optional[dict] = None):
        """Результат OCR: подставляется и как черновик ответа — человеку остается проверить."""
        import json
        with self.conn() as c:
            c.execute("UPDATE cards SET side=?, status=?, pred_name=?, pred_arc=?, pred_country=?, pred_address=?,"
                      " name=?, arc=?, country=?, address=?, rotation=?, regions=? WHERE id=? AND status=?",
                      (side, READY, name, arc, country, address, name, arc, country, address, rotation,
                       json.dumps(regions or {}), card_id, NEW))

    def save_label(self, card_id: int, side: str, name="", arc="", country="", address="",
                   rotation: Optional[int] = None, regions: Optional[dict] = None):
        import json
        sets, args = ("side=?, status=?, name=?, arc=?, country=?, address=?, checked_at=?",
                      [side, DONE if side in (FRONT, BACK) else SKIP, name.strip(), arc.strip(), country.strip(),
                       " ".join(address.split()), datetime.now().isoformat(timespec="seconds")])
        if rotation is not None:
            sets += ", rotation=?"
            args.append(rotation % 360)
        if regions is not None:
            sets += ", regions=?"
            args.append(json.dumps(regions))
        with self.conn() as c:
            c.execute(f"UPDATE cards SET {sets} WHERE id=?", (*args, card_id))

    def save_draft(self, card_id: int, side: str, name="", arc="", country="", address="",
                   rotation: int = 0, regions: Optional[dict] = None):
        """Исправления без «Верно»: сохраняются, статус карты не меняется."""
        import json
        with self.conn() as c:
            c.execute("UPDATE cards SET side=?, name=?, arc=?, country=?, address=?, rotation=?, regions=?"
                      " WHERE id=? AND status IN (?, ?)",
                      (side, name.strip(), arc.strip(), country.strip(), " ".join(address.split()), rotation % 360,
                       json.dumps(regions or {}), card_id, READY, DONE))

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
    """(сторона, поля) — что OCR видит на фото: лицо (ФИО, номер, страна) или оборот (адрес).
    В полях также rotation (как повернуть фото) и regions (где на фото каждое поле)."""
    from . import services
    from .ocr import detect

    def front():
        card, _lines, _img, _raw, angle = engine.read_front_detail(path)
        vals = {"name": card.name, "arc": card.reg_no, "country": card.country}
        return FRONT, {**vals, "rotation": angle, "regions": find_regions(engine, path, FRONT, vals, angle)}

    def back():
        addr, _w = services._read_back(engine, Path(path))
        _lines, _img, _raw, angle = back_detail(engine, path)
        vals = {"address": addr}
        return BACK, {**vals, "rotation": angle, "regions": find_regions(engine, path, BACK, vals, angle)}

    kind, _reason = detect.classify_photo(engine.read_image_lines(path))
    if kind == detect.FRONT:
        return front()
    if kind == detect.BACK:
        return back()
    # Тип не определился (фото повернуто, пестрый фон) — пробуем прочитать как лицо, затем как оборот.
    side, fields = front()
    if fields["arc"] or (fields["name"] and fields["country"]):
        return side, fields
    side, fields = back()
    if fields["address"]:
        return side, fields
    return OTHER, {}


def _field_boxes(raw, side: str, vals: dict) -> dict:
    """{поле: [рамки]} — какие строки OCR относятся к каждому полю."""
    out = {}
    arc = re.sub(r"\D", "", vals.get("arc", ""))
    for box, text, _c in raw:
        if side == FRONT:
            if re.search(r"[가-힣]", text):
                continue
            digits = re.sub(r"\D", "", text)
            if len(digits) >= 10:
                if len(arc) == 13 and (digits[:6] == arc[:6] or digits[-7:] == arc[-7:]):
                    out.setdefault("arc", []).append(box)
                continue
            country = vals.get("country", "")
            if country and match_segment(text, country, 0.75) == _norm(country):
                out.setdefault("country", []).append(box)
                continue
            if vals.get("name") and match_segment(text, vals["name"]):
                out.setdefault("name", []).append(box)
        elif side == BACK and vals.get("address"):
            if not re.search(r"[가-힣]", text) or re.search(r"(?:장|사무소|출입국|외국인청)\s*$", text.strip()):
                continue
            body = re.sub(r"^\s*(?:19|20)\d{2}[.\s]\d{1,2}[.\s]\d{1,2}\.?\s*", "", text)
            if len(re.sub(r"\s", "", body)) >= 4 and match_segment(body, vals["address"]):
                out.setdefault("address", []).append(box)
    return out


def find_regions(engine, path: Path, side: str, vals: dict, rotation: int) -> dict:
    """Области полей на фото (повернутом на rotation), в долях 0..1 — чтобы показать и поправить."""
    from .ocr.ocr_engine import load_image, resize_max

    photo = load_image(path)
    img = resize_max(photo.rotate(rotation, expand=True) if rotation else photo)
    raw = engine.readtext(img, latin=side == FRONT)
    regions = {}
    for field, boxes in _field_boxes(raw, side, vals).items():
        xs = [p[0] for b in boxes for p in b]
        ys = [p[1] for b in boxes for p in b]
        regions[field] = [round(max(0, min(xs)) / img.width, 4), round(max(0, min(ys)) / img.height, 4),
                          round(min(img.width, max(xs)) / img.width, 4), round(min(img.height, max(ys)) / img.height, 4)]
    return regions


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


# ------------------------------------------------------------ учебный набор строк

LATIN, KOREAN = "latin", "korean"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().upper()


def _segments(label: str):
    """Куски правильного ответа, которыми может быть одна строка на карте: целые слова подряд,
    а на краях — часть слова (перенос: «IBRA-» / «TBEK», «시동3리2» / «길 9-1»)."""
    words = label.split()
    n = len(words)
    latin = label.isascii()  # латинское слово на карте переносится только с дефисом
    for i in range(n):
        for j in range(i + 1, n + 1):
            seg = " ".join(words[i:j])
            yield seg
            last = words[j - 1]
            for k in range(1, len(last)):               # конец строки — начало слова
                head = " ".join(words[i:j - 1] + [last[:k]])
                if not latin:
                    yield head
                yield head + "-"
            first = words[i]
            for k in range(1, len(first)):              # начало строки — конец слова
                yield " ".join([first[k:]] + words[i + 1:j])


def match_segment(ocr_text: str, label: str, min_ratio: float = 0.72) -> str:
    """Какой кусок правильного ответа написан в этой строке карты ('' — не похоже)."""
    from difflib import SequenceMatcher

    t = _norm(ocr_text)
    if len(t.replace(" ", "")) < 2:
        return ""
    best, best_r = "", 0.0
    for seg in _segments(_norm(label)):
        if abs(len(seg) - len(t)) > max(4, len(t) // 2):
            continue
        r = SequenceMatcher(None, t, seg).ratio()
        if r > best_r or (r == best_r and len(seg) > len(best)):
            best, best_r = seg, r
    return best if best_r >= min_ratio else ""


def _crop(img, box, pad_ratio: float = 0.15):
    xs, ys = [p[0] for p in box], [p[1] for p in box]
    h = max(ys) - min(ys)
    pad = pad_ratio * h
    return img.crop((max(0, min(xs) - pad), max(0, min(ys) - pad),
                     min(img.width, max(xs) + pad), min(img.height, max(ys) + pad)))


def back_detail(engine, path: Path):
    """Оборот в лучшем положении: (строки, изображение, фрагменты OCR, поворот) — как при приемке."""
    from .ocr import back as back_ocr
    from .ocr.ocr_engine import crop_card, group_lines, load_image, resize_max

    card = resize_max(crop_card(load_image(path)))
    best = None
    for angle in (0, 90, 270, 180):
        img = card.rotate(angle, expand=True) if angle else card
        raw = engine.readtext(img)
        lines = group_lines(raw)
        key = back_ocr.orientation_score(lines)
        if best is None or key > best[0]:
            best = (key, lines, img, raw, angle)
    return best[1], best[2], best[3], best[4]


def _labels(card: Card) -> dict:
    fields = FIELDS_FRONT if card.side == FRONT else FIELDS_BACK
    vals = {f: getattr(card, f) for f in fields if getattr(card, f)}
    if vals.get("arc"):
        d = re.sub(r"\D", "", vals["arc"])
        vals["arc"] = f"{d[:6]}-{d[6:]}" if len(d) == 13 else vals["arc"]
    return vals


def _lines_from_boxes(engine, img, raw, side: str, vals: dict, only=None):
    model = LATIN if side == FRONT else KOREAN
    out = []
    for field, boxes in _field_boxes(raw, side, vals).items():
        if only and field not in only:
            continue
        for box in boxes:
            text = next(t for b, t, _c in raw if b is box)
            if field == "arc":
                label = vals["arc"]
            elif field == "country":
                label = _norm(vals["country"])
            else:
                body = re.sub(r"^\s*(?:19|20)\d{2}[.\s]\d{1,2}[.\s]\d{1,2}\.?\s*", "", text)
                label = match_segment(body, vals[field])
            if label and len(label.replace(" ", "")) >= 3:
                out.append((model, engine.upright(_crop(img, box)), label))
    return out


def card_lines(engine, card: Card):
    """[(модель, картинка строки, правильный текст)] с одной размеченной карты.
    Если на карте отмечены области полей — строки ищутся только внутри них."""
    from .ocr.ocr_engine import load_image, resize_max

    if card.side not in (FRONT, BACK):
        return []
    vals = _labels(card)
    if not vals:
        return []
    out = []
    regions = {f: r for f, r in card.region_map.items() if f in vals}
    if regions:
        photo = load_image(card.path)
        photo = photo.rotate(card.rotation, expand=True) if card.rotation else photo
        for field, (x0, y0, x1, y1) in regions.items():
            w, h = photo.width, photo.height
            pad = 0.01
            box = (max(0, (x0 - pad) * w), max(0, (y0 - pad) * h), min(w, (x1 + pad) * w), min(h, (y1 + pad) * h))
            if box[2] - box[0] < 8 or box[3] - box[1] < 8:
                continue
            if field in ("arc", "country"):  # однострочное поле: рамка и есть строка
                label = vals["arc"] if field == "arc" else _norm(vals["country"])
                out.append((LATIN, engine.upright(photo.crop(box)), label))
                continue
            crop = resize_max(photo.crop(box), target=1600, upscale=True)
            raw = engine.readtext(crop, latin=card.side == FRONT)
            out += _lines_from_boxes(engine, crop, raw, card.side, {field: vals[field]}, only={field})
    rest = {f: v for f, v in vals.items() if f not in regions}
    if rest:  # поля без отмеченной области — по всей карте, как при приемке
        if card.side == FRONT:
            _data, _lines, img, raw, _angle = engine.read_front_detail(card.path)
        else:
            _lines, img, raw, _angle = back_detail(engine, card.path)
        out += _lines_from_boxes(engine, img, raw, card.side, rest, only=set(rest))
    return out


def build_dataset(store: TrainingStore, engine=None, out_dir: Optional[Path] = None,
                  progress: Callable[[str], None] = lambda _m: None) -> dict:
    """Вырезает строки из проверенных карт: out_dir/{latin,korean}/{train,val}.txt + images/.
    Каждая пятая карта (по содержимому фото) — проверочная, в обучение не попадает.
    Строки ищет стандартный OCR: тогда набор одинаков при любой подключенной модели и сравнение честное."""
    from .ocr.ocr_engine import OcrEngine

    engine = engine or OcrEngine(trained=False)
    out_dir = Path(out_dir or training_dir() / "dataset")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    counts = {f"{m}_{s}": 0 for m in (LATIN, KOREAN) for s in ("train", "val")}
    files = {}
    cards = [c for c in store.cards(DONE) if c.side in (FRONT, BACK)]
    for i, card in enumerate(cards, 1):
        progress(f"Строки с карт {i}/{len(cards)}")
        split = "val" if int(card.image_hash[:8], 16) % 5 == 0 else "train"
        try:
            items = card_lines(engine, card)
        except Exception:  # noqa: BLE001 — одна битая карта не останавливает сборку
            continue
        for n, (model, crop, text) in enumerate(items):
            folder = out_dir / model / "images"
            folder.mkdir(parents=True, exist_ok=True)
            name = f"{card.id:05d}_{n:02d}.png"
            crop.convert("RGB").save(folder / name)
            key = (model, split)
            files.setdefault(key, []).append(f"images/{name}\t{text}")
            counts[f"{model}_{split}"] += 1
    for (model, split), rows in files.items():
        (out_dir / model / f"{split}.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return counts
