"""Синхронизация между компьютерами через общую папку в локальной сети (\\\\SERVER\\E9pay).

У каждого компьютера своя база; «Обновить» обменивается изменениями через общую папку:
  E9pay-sync/clients/<uid>/client.json   — поля клиента с временем изменения + список документов
  E9pay-sync/clients/<uid>/files/<sha1>  — файлы (копируются один раз)
  E9pay-sync/computers/<имя>.json        — какие клиенты есть на компьютере (значки 🌐 / 🖥)
  E9pay-sync/rules/<имя>.json            — типы документов
  E9pay-sync/model/                      — дообученные модели OCR и словарь имен

Правила: поле с более поздним изменением побеждает; документы объединяются; удаление — только
на своем компьютере (удаленное запоминается и обратно не забирается). Через интернет ничего не идет.
"""

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import config
from .db import SYNC_FIELDS, Database, DocRule, utc_now

ROOT_NAME = "E9pay-sync"
FIELD_NAMES = {"full_name": "ФИО", "arc_number": "номер ARC", "birth_date": "дата рождения", "nationality": "страна",
               "registered_address": "адрес", "bank_name": "банк", "account_number": "счет", "phone": "телефон",
               "email": "e-mail", "batch_period": "месяц", "printed": "напечатан"}
LOCK_STALE_SECONDS = 180

Progress = Callable[[str], None]


class SyncError(Exception):
    pass


@dataclass
class SyncResult:
    sent_clients: int = 0
    sent_files: int = 0
    new_clients: List[str] = field(default_factory=list)
    updated_clients: List[str] = field(default_factory=list)
    received_files: int = 0
    conflicts: List[str] = field(default_factory=list)
    models: List[str] = field(default_factory=list)
    computers: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = []
        if self.new_clients or self.updated_clients:
            parts.append(f"получено: новых {len(self.new_clients)}, изменено {len(self.updated_clients)}")
        if self.sent_clients:
            parts.append(f"отправлено: {self.sent_clients}")
        return " · ".join(parts) or "изменений нет"


# ====================================================================== общее

def _root(shared: Path) -> Path:
    shared = Path(shared)
    if not shared.is_dir():
        raise SyncError(f"Общая папка недоступна: {shared}\nПроверьте, что сервер включен и папка открыта.")
    root = shared / ROOT_NAME
    for sub in ("clients", "computers", "rules", "model"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def _write_json(path: Path, data):
    """Запись через временный файл: другой компьютер не увидит наполовину записанный файл."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def file_sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _copy(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.tmp")
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


class _Lock:
    """Один компьютер синхронизирует за раз (замок с именем и временем; старый замок не мешает)."""

    def __init__(self, root: Path, me: str):
        self.path, self.me = root / "sync.lock", me

    def __enter__(self):
        info = _read_json(self.path, {}) or {}
        if info.get("computer") not in (None, self.me):
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(info["time"])).total_seconds()
            except (KeyError, ValueError):
                age = LOCK_STALE_SECONDS + 1
            if age < LOCK_STALE_SECONDS:
                raise SyncError(f"Сейчас синхронизируется «{info['computer']}» — повторите через минуту.")
        _write_json(self.path, {"computer": self.me, "time": utc_now()})
        return self

    def __exit__(self, *exc):
        try:
            if (_read_json(self.path, {}) or {}).get("computer") == self.me:
                self.path.unlink()
        except OSError:
            pass


# ====================================================================== подготовка

def ensure_ids(db: Database, progress: Progress = lambda _m: None):
    """У каждого клиента — постоянный uid, у каждого файла — SHA-1.
    uid клиента с номером ARC зависит от ARC и месяца: один и тот же архив, загруженный на двух
    компьютерах, становится одним клиентом, а не двумя."""
    used = set()
    clients = db.all_clients()
    for c in clients:
        if c.uid:
            used.add(c.uid)
    for c in clients:
        if not c.uid:
            digits = "".join(ch for ch in c.arc_number or "" if ch.isdigit())
            uid = ("arc-" + hashlib.sha1(f"{digits}|{c.batch_period}".encode()).hexdigest()[:16]
                   if len(digits) == 13 else uuid.uuid4().hex)
            if uid in used:
                uid = uuid.uuid4().hex
            used.add(uid)
            db.set_uid(c.id, uid)
        for d in db.documents(c.id):
            if not d.file_hash and d.path.exists():
                progress(f"Синхронизация: проверяю файлы · {d.path.name}")
                db.set_doc_hash(d.id, file_sha1(d.path))


def _times(c) -> dict:
    try:
        return json.loads(c.field_times or "{}")
    except ValueError:
        return {}


def _value(c, f):
    return c.warnings if f == "warnings" else getattr(c, f, "")


def _payload(db: Database, c) -> dict:
    times = _times(c)
    fields = {f: [_value(c, f)] + list(times.get(f, ["", ""])) for f in SYNC_FIELDS}
    docs = []
    for d in db.documents(c.id):
        if not d.file_hash or not d.path.exists():
            continue
        try:
            rel = d.path.resolve().relative_to(c.folder.resolve()).as_posix()
        except (ValueError, OSError):
            rel = d.path.name
        docs.append({"hash": d.file_hash, "ext": d.path.suffix.lower(), "rel": rel, "doc_type": d.doc_type,
                     "original_name": d.original_name, "is_processed_scan": d.is_processed_scan,
                     "password_status": d.password_status, "detected_by": d.detected_by,
                     "added_at": d.added_at})
    return {"uid": c.uid, "created_at": c.created_at, "fields": fields, "docs": docs}


def _merge_payloads(old: Optional[dict], new: dict) -> dict:
    """client.json в общей папке: по каждому полю — более позднее изменение, документы — все."""
    if not old:
        return new
    fields = dict(old.get("fields", {}))
    for f, v in new["fields"].items():
        if f not in fields or (v[1] or "") > (fields[f][1] or ""):
            fields[f] = v
    docs = {d["hash"]: d for d in old.get("docs", [])}
    for d in new["docs"]:
        docs.setdefault(d["hash"], d)
    return {"uid": new["uid"], "created_at": old.get("created_at") or new.get("created_at"), "fields": fields,
            "docs": list(docs.values())}


# ====================================================================== прием

def _apply_client(db: Database, root: Path, data: dict, last_sync: str, res: SyncResult, ignored: set):
    from .services import _unique, move_clients

    uid = data["uid"]
    if (uid, "") in ignored:
        return
    remote = data.get("fields", {})
    c = db.client_by_uid(uid)
    if c is None:  # новый клиент с другого компьютера
        values = {f: v[0] for f, v in remote.items() if f in SYNC_FIELDS}
        period = values.pop("batch_period", "") or "0000-00"
        warnings = values.pop("warnings", []) or []
        name = values.get("full_name") or "клиент"
        folder = _unique(config.period_dir(period) / config.safe_name(name))
        folder.mkdir(parents=True, exist_ok=True)
        times = {f: v[1:] for f, v in remote.items() if f in SYNC_FIELDS and v[1]}
        cid = db.add_client(period, warnings=warnings, folder_path=str(folder), uid=uid,
                            field_times=json.dumps(times, ensure_ascii=False),
                            **({"created_at": data["created_at"]} if data.get("created_at") else {}),
                            **{k: v for k, v in values.items() if v is not None})
        c = db.client(cid)
        res.new_clients.append(name)
    else:
        local_t = _times(c)
        updates, times, changed = {}, {}, False
        for f, v in remote.items():
            if f not in SYNC_FIELDS or len(v) < 3:
                continue
            value, ts, who = v[0], v[1] or "", v[2]
            mine = (local_t.get(f) or ["", ""])
            if value == _value(c, f):
                continue
            both = (mine[0] or "") > last_sync and ts > last_sync and f not in ("warnings", "status", "stage")
            if ts <= (mine[0] or ""):
                if both:  # изменено на обоих, здесь позже — остается значение этого компьютера
                    res.conflicts.append(f"{c.full_name or 'клиент'} — {FIELD_NAMES.get(f, f)}: оставлено «{_value(c, f)}» "
                                         f"(этот компьютер), а не «{value}» ({who})")
                continue
            if both:
                res.conflicts.append(f"{c.full_name or 'клиент'} — {FIELD_NAMES.get(f, f)}: «{value}» ({who}) "
                                     f"вместо «{_value(c, f)}» (этот компьютер)")
            if f == "batch_period":
                move_clients(db, [c.id], value)
                db._stamp(c.id, {"batch_period": [ts, who]})
            else:
                updates[f], times[f] = value, [ts, who]
            changed = True
        if updates:
            db.update_client(c.id, _times=times, **updates)
        if changed:
            res.updated_clients.append(c.full_name or "клиент")
        c = db.client(c.id)
    _apply_docs(db, root, c, data.get("docs", []), res, ignored)


def _apply_docs(db: Database, root: Path, c, docs: List[dict], res: SyncResult, ignored: set):
    from .services import _unique

    local = db.documents(c.id)
    have = {d.file_hash for d in local if d.file_hash}
    # обработанный скан (лист ID на А4, скан оборота) у клиента один на тип — новее заменяет старый
    newest_scan = {}
    for d in docs:
        if d.get("is_processed_scan"):
            cur = newest_scan.get(d["doc_type"])
            if cur is None or (d.get("added_at") or "") > (cur.get("added_at") or ""):
                newest_scan[d["doc_type"]] = d
    for d in docs:
        h = d["hash"]
        if h in have or (c.uid, h) in ignored:
            continue
        if d.get("is_processed_scan"):
            if newest_scan.get(d["doc_type"]) is not d:
                continue
            scans = [o for o in db.documents(c.id) if o.is_processed_scan and o.doc_type == d["doc_type"]]
            if any((o.added_at or "") >= (d.get("added_at") or "") for o in scans):
                continue  # здесь скан новее
            for o in scans:
                db.delete_document(o.id)
        src = root / "clients" / c.uid / "files" / f"{h}{d.get('ext', '')}"
        if not src.exists():
            continue
        rel = Path(*[p for p in Path(d.get("rel") or src.name).parts if p not in ("..", "/", "\\")])
        dst = _unique(c.folder / rel)
        try:
            _copy(src, dst)
        except OSError as e:
            res.errors.append(f"{c.full_name}: {dst.name} — {e}")
            continue
        doc_id = db.add_document(c.id, d.get("doc_type", ""), dst, original_name=d.get("original_name", ""),
                                 is_processed_scan=int(d.get("is_processed_scan") or 0),
                                 password_status=d.get("password_status", ""), detected_by=d.get("detected_by", ""),
                                 added_at=d.get("added_at") or None)
        db.set_doc_hash(doc_id, h)
        have.add(h)
        res.received_files += 1


# ====================================================================== типы документов и модели OCR

def _sync_rules(db: Database, root: Path, me: str):
    rules = db.rules()
    _write_json(root / "rules" / f"{config.safe_name(me)}.json",
                [{"code": r.code, "doc_name": r.doc_name, "filename_patterns": r.filename_patterns,
                  "ocr_keywords": r.ocr_keywords, "in_master": r.in_master, "print_step3": r.print_step3}
                 for r in rules if not r.builtin])
    codes = {r.code for r in rules}
    order = max((r.sort_order for r in rules if r.sort_order < 100), default=0)
    for f in (root / "rules").glob("*.json"):
        for r in _read_json(f, []) or []:
            if r.get("code") and r["code"] not in codes:  # тип, которого здесь нет — иначе документы «не определен»
                order += 1
                db.save_rule(DocRule(id=0, code=r["code"], doc_name=r["doc_name"],
                                     filename_patterns=r.get("filename_patterns", []),
                                     ocr_keywords=r.get("ocr_keywords", []), sort_order=order,
                                     in_master=int(r.get("in_master", 1)), print_step3=int(r.get("print_step3", 0)),
                                     builtin=0))
                codes.add(r["code"])


def _sync_models(root: Path, me: str, res: SyncResult):
    """Модель, обученная на этом компьютере, уходит в общую папку; новее с другого — приходит сюда."""
    from . import ocr_trainer, training

    model_dir = root / "model"
    shared_h = _read_json(model_dir / "history.json", {}) or {}
    local_h = ocr_trainer.history()
    active = ocr_trainer.active_dir()
    changed_shared = False
    for key, entry in local_h.items():  # отправить свое
        src = active / f"{key}.onnx"
        if entry.get("computer") == me and src.exists() and entry.get("date", "") > shared_h.get(key, {}).get("date", ""):
            _copy(src, model_dir / f"{key}.onnx")
            shared_h[key] = entry
            changed_shared = True
    if changed_shared:
        _write_json(model_dir / "history.json", shared_h)
        try:  # словарь имен из своей учебной базы
            names = sorted({c.name for c in training.TrainingStore().cards(training.DONE)
                            if c.side == training.FRONT and c.name})
            _write_json(model_dir / "names.json", names)
        except Exception:  # noqa: BLE001
            pass
    got = False
    for key, entry in shared_h.items():  # забрать чужое, если новее
        src = model_dir / f"{key}.onnx"
        if entry.get("computer") in (None, me) or not src.exists():
            continue
        if entry.get("date", "") <= local_h.get(key, {}).get("date", ""):
            continue
        active.mkdir(parents=True, exist_ok=True)
        dst = active / f"{key}.onnx"
        if dst.exists():
            shutil.copy2(dst, active / f"{key}.prev.onnx")  # откат — как после своего обучения
        _copy(src, dst)
        local_h[key] = entry
        res.models.append(f"{ocr_trainer.MODELS.get(key, {}).get('title', key)} (с «{entry.get('computer')}»)")
        got = True
    if got:
        (active / "history.json").write_text(json.dumps(local_h, ensure_ascii=False, indent=1), encoding="utf-8")
        ocr_trainer._reset_engine()
    names = model_dir / "names.json"
    if names.exists():
        try:
            _copy(names, training.training_dir() / "synced_names.json")
        except OSError:
            pass


# ====================================================================== синхронизация целиком

def synchronize(db: Database, shared: Path, progress: Progress = lambda _m: None) -> SyncResult:
    me = config.computer_name()
    res = SyncResult()
    root = _root(shared)
    with _Lock(root, me):
        progress("Синхронизация: подготовка")
        ensure_ids(db, progress)
        last_sync = db.meta("sync_last", "")
        started = utc_now()
        ignored = db.ignored()
        # 1. принять изменения других компьютеров
        dirs = [d for d in (root / "clients").iterdir() if d.is_dir()]
        for i, d in enumerate(dirs, 1):
            data = _read_json(d / "client.json")
            if not data or not data.get("uid"):
                continue
            progress(f"Синхронизация: получаю {i}/{len(dirs)}")
            try:
                _apply_client(db, root, data, last_sync, res, ignored)
            except Exception as e:  # noqa: BLE001 — один клиент не останавливает остальных
                res.errors.append(f"{data.get('fields', {}).get('full_name', ['?'])[0]}: {e}")
        _sync_rules(db, root, me)
        # 2. отправить свое
        mine = []
        clients = db.all_clients()
        for i, c in enumerate(clients, 1):
            if not c.uid or (c.uid, "") in ignored:
                continue
            mine.append(c.uid)
            progress(f"Синхронизация: отправляю {i}/{len(clients)}")
            folder = root / "clients" / c.uid
            payload = _payload(db, c)
            for doc in payload["docs"]:
                dst = folder / "files" / f"{doc['hash']}{doc['ext']}"
                if not dst.exists():
                    local = next((x for x in db.documents(c.id) if x.file_hash == doc["hash"]), None)
                    if local and local.path.exists():
                        _copy(local.path, dst)
                        res.sent_files += 1
            old = _read_json(folder / "client.json")
            merged = _merge_payloads(old, payload)
            if merged != old:
                folder.mkdir(parents=True, exist_ok=True)
                _write_json(folder / "client.json", merged)
                res.sent_clients += 1
        # 3. кто что имеет (значки 🌐 / 🖥) и модели OCR
        _write_json(root / "computers" / f"{config.safe_name(me)}.json",
                    {"name": me, "last_sync": started, "clients": mine})
        presence: Dict[str, List[str]] = {}
        for f in (root / "computers").glob("*.json"):
            info = _read_json(f, {}) or {}
            res.computers.append(info.get("name", f.stem))
            for uid in info.get("clients", []):
                presence.setdefault(uid, []).append(info.get("name", f.stem))
        try:
            _sync_models(root, me, res)
        except Exception as e:  # noqa: BLE001
            res.errors.append(f"модель OCR: {e}")
        db.set_meta("sync_presence", json.dumps(presence, ensure_ascii=False))
        db.set_meta("sync_computers", json.dumps(sorted(res.computers), ensure_ascii=False))
        db.set_meta("sync_last", started)
        db.set_meta("sync_last_local", datetime.now().strftime("%Y-%m-%d %H:%M"))
    return res


def presence(db: Database) -> Dict[str, List[str]]:
    """uid → на каких компьютерах есть клиент (по последней синхронизации)."""
    try:
        return json.loads(db.meta("sync_presence", "{}"))
    except ValueError:
        return {}


def computers(db: Database) -> List[str]:
    try:
        return json.loads(db.meta("sync_computers", "[]"))
    except ValueError:
        return []
