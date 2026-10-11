"""Передача клиентов между компьютерами одним файлом .e9pay — без сети и общих папок.

Файл — ZIP (по желанию с паролем AES-256, открывается и 7-Zip): manifest.json с данными
клиентов и типов документов + все файлы клиентов. При приеме ничего не распознается заново.
"""

import json
import shutil
import socket
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Callable, Dict, List, Optional

import pyzipper

from . import config
from .db import CLIENT_FIELDS, Client, Database, DocRule
from .services import _refresh_status, _unique, delete_client_everything, recent_duplicates

FORMAT = "e9pay-transfer"
VERSION = 1
EXTENSION = ".e9pay"

ACTION_NEW, ACTION_REPLACE, ACTION_SKIP, ACTION_MERGE = "new", "replace", "skip", "merge"

Progress = Callable[[str], None]


def _noop(_m):
    pass


class TransferError(Exception):
    pass


class PasswordNeeded(TransferError):
    pass


# ====================================================================== экспорт

def export_clients(db: Database, client_ids: List[int], out_path: Path, password: str = "",
                   progress: Progress = _noop) -> Path:
    out_path = Path(out_path)
    if out_path.suffix.lower() != EXTENSION:
        out_path = out_path.with_suffix(EXTENSION)
    rules = db.rules()
    manifest = {
        "format": FORMAT, "version": VERSION,
        "created": datetime.now().isoformat(timespec="seconds"),
        "computer": socket.gethostname(),
        "doc_rules": [{"code": r.code, "doc_name": r.doc_name, "filename_patterns": r.filename_patterns,
                       "ocr_keywords": r.ocr_keywords, "in_master": r.in_master, "print_step3": r.print_step3,
                       "builtin": r.builtin} for r in rules],
        "clients": [],
    }
    files = []  # (путь на диске, путь в архиве)
    for n, cid in enumerate(client_ids, start=1):
        c = db.client(cid)
        if not c:
            continue
        progress(f"{n}/{len(client_ids)}: {c.full_name}")
        base = f"clients/{n:03d}_{config.safe_name(c.full_name or 'client')}"
        entry = {k: getattr(c, k) for k in CLIENT_FIELDS if k not in ("folder_path", "master_pdf")}
        entry.update(batch_period=c.batch_period, warnings=c.warnings, created_at=c.created_at, documents=[])
        folder = c.folder

        def arc_name(path: Path) -> str:
            try:
                rel = path.resolve().relative_to(folder.resolve()).as_posix()
            except ValueError:
                rel = f"other/{path.name}"
            return f"{base}/{rel}"

        for d in db.documents(cid):
            if not d.path.exists():
                continue
            name = arc_name(d.path)
            files.append((d.path, name))
            entry["documents"].append({"doc_type": d.doc_type, "file": name, "original_name": d.original_name,
                                       "is_processed_scan": d.is_processed_scan,
                                       "password_status": d.password_status, "detected_by": d.detected_by})
        if c.master_pdf and Path(c.master_pdf).exists():
            entry["master_pdf"] = arc_name(Path(c.master_pdf))
            files.append((Path(c.master_pdf), entry["master_pdf"]))
        # обработанные сканы (PNG), которые не числятся документами
        scans = folder / "scans"
        if scans.exists():
            for f in scans.iterdir():
                if f.is_file():
                    files.append((f, arc_name(f)))
        manifest["clients"].append(entry)

    tmp = out_path.with_suffix(".tmp")
    if password:
        zf = pyzipper.AESZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, encryption=pyzipper.WZ_AES)
        zf.setpassword(password.encode("utf-8"))
    else:
        zf = zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED)
    with zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
        seen = set()
        for src, name in files:
            if name not in seen:
                seen.add(name)
                zf.write(src, name)
    tmp.replace(out_path)
    return out_path


# ====================================================================== прием

@dataclass
class IncomingClient:
    index: int
    data: dict
    duplicates: List[Client] = field(default_factory=list)      # меньше года назад
    old_duplicates: List[Client] = field(default_factory=list)  # больше года назад
    action: str = ACTION_NEW

    @property
    def title(self) -> str:
        d = self.data
        return f"{d.get('full_name') or 'без имени'} · ARC {d.get('arc_number') or '—'} · период {d.get('batch_period')}"


@dataclass
class Package:
    path: Path
    password: str
    manifest: dict
    clients: List[IncomingClient]

    @property
    def info(self) -> str:
        m = self.manifest
        return f"создан {m.get('created', '')[:16].replace('T', ' ')} на компьютере {m.get('computer', '?')}"


def _open_zip(path: Path, password: str):
    zf = pyzipper.AESZipFile(path)
    if password:
        zf.setpassword(password.encode("utf-8"))
    return zf


def is_encrypted(path: Path) -> bool:
    with zipfile.ZipFile(path) as zf:
        return any(i.flag_bits & 0x1 for i in zf.infolist())


def read_package(db: Database, path: Path, password: str = "") -> Package:
    """Читает файл передачи и проверяет каждого клиента на повторы по ARC."""
    path = Path(path)
    try:
        if is_encrypted(path) and not password:
            raise PasswordNeeded("Файл защищен паролем")
        with _open_zip(path, password) as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    except PasswordNeeded:
        raise
    except RuntimeError as e:  # неверный пароль
        raise PasswordNeeded("Неверный пароль") from e
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as e:
        raise TransferError(f"Это не файл передачи E9pay или он поврежден: {path.name}") from e
    if manifest.get("format") != FORMAT:
        raise TransferError("Это не файл передачи E9pay")
    clients = []
    for i, data in enumerate(manifest.get("clients", [])):
        recent, old = recent_duplicates(db, data.get("arc_number", ""))
        ic = IncomingClient(i, data, recent, old)
        ic.action = ACTION_REPLACE if recent else ACTION_NEW
        clients.append(ic)
    return Package(path, password, manifest, clients)


def _merge_rules(db: Database, manifest: dict) -> Dict[str, str]:
    """Типы документов отправителя -> коды у получателя; недостающие типы добавляются."""
    mine = db.rules()
    by_code = {r.code: r for r in mine}
    by_name = {r.doc_name.strip().lower(): r for r in mine}
    mapping = {"": ""}
    order = max((r.sort_order for r in mine if r.in_master), default=0)
    for r in manifest.get("doc_rules", []):
        code, name = r["code"], r["doc_name"]
        if r.get("builtin") and code in by_code:
            mapping[code] = code
        elif name.strip().lower() in by_name:
            mapping[code] = by_name[name.strip().lower()].code
        elif code in by_code and not code.startswith("custom_"):
            mapping[code] = code
        else:
            order += 1
            new_id = db.save_rule(DocRule(0, "", name, r.get("filename_patterns", []), r.get("ocr_keywords", []),
                                          order, int(r.get("in_master", 1)), int(r.get("print_step3", 0)), 0))
            new = next(x for x in db.rules() if x.id == new_id)
            mapping[code] = new.code
            by_name[name.strip().lower()] = new
    # новые типы встают в конец очереди Master-PDF, перед встроенными фото-типами
    rules = db.rules()
    db.reorder_rules([x.id for x in sorted(rules, key=lambda x: (x.builtin and not x.in_master, x.sort_order))])
    return mapping


def import_package(db: Database, pkg: Package, progress: Progress = _noop) -> List[int]:
    """Принимает клиентов по выбранным действиям. Возвращает id принятых/обновленных клиентов."""
    mapping = _merge_rules(db, pkg.manifest)
    done = []
    with _open_zip(pkg.path, pkg.password) as zf:
        names = set(zf.namelist())

        def extract(member: str, folder: Path) -> Optional[Path]:
            if member not in names:
                return None
            parts = PurePosixPath(member).parts[2:]  # без clients/NNN_name/
            parts = [p for p in parts if p not in ("", ".", "..")]
            target = _unique(folder.joinpath(*parts)) if parts else _unique(folder / PurePosixPath(member).name)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            return target

        for n, ic in enumerate(pkg.clients, start=1):
            if ic.action == ACTION_SKIP:
                continue
            d = ic.data
            progress(f"{n}/{len(pkg.clients)}: {d.get('full_name')}")
            if ic.action == ACTION_MERGE and ic.duplicates:
                target = ic.duplicates[0]
                existing = {doc.original_name for doc in db.documents(target.id)}
                for doc in d.get("documents", []):
                    if doc.get("original_name") in existing or doc.get("is_processed_scan"):
                        continue
                    path = extract(doc["file"], target.folder)
                    if path:
                        db.add_document(target.id, mapping.get(doc.get("doc_type", ""), ""), path,
                                        original_name=doc.get("original_name", ""),
                                        password_status=doc.get("password_status", ""),
                                        detected_by=(doc.get("detected_by") or "") + " · из файла передачи")
                _refresh_status(db, target.id)
                done.append(target.id)
                continue
            if ic.action == ACTION_REPLACE:
                for c in ic.duplicates:
                    delete_client_everything(db, c.id)
            period = d.get("batch_period") or datetime.now().strftime("%Y-%m")
            name = d.get("full_name") or "client"
            arc6 = (d.get("arc_number") or "")[:6]
            folder = _unique(config.period_dir(period) / config.safe_name(f"{name}_{arc6}" if arc6 else name))
            folder.mkdir(parents=True)
            fields = {k: d.get(k) for k in CLIENT_FIELDS if k in d and k not in ("folder_path", "master_pdf")}
            cid = db.add_client(period, folder_path=str(folder), warnings=d.get("warnings", []),
                                created_at=d.get("created_at") or None, **fields)
            for doc in d.get("documents", []):
                path = extract(doc["file"], folder)
                if path:
                    db.add_document(cid, mapping.get(doc.get("doc_type", ""), ""), path,
                                    original_name=doc.get("original_name", ""),
                                    is_processed_scan=int(doc.get("is_processed_scan", 0)),
                                    password_status=doc.get("password_status", ""),
                                    detected_by=doc.get("detected_by", ""))
            # остальные файлы (сканы PNG, Master-PDF)
            prefix = PurePosixPath(doc["file"]).parts[:2] if d.get("documents") else None
            if d.get("master_pdf"):
                mp = extract(d["master_pdf"], folder)
                if mp:
                    db.update_client(cid, master_pdf=str(mp))
            if prefix:
                base = "/".join(prefix) + "/scans/"
                have = {Path(x.file_path).name for x in db.documents(cid)}
                for member in names:
                    if member.startswith(base) and PurePosixPath(member).name not in have:
                        extract(member, folder)
            done.append(cid)
    return done
