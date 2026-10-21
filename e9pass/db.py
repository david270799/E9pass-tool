"""SQLite: клиенты, их документы и правила (типы) документов."""

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_period TEXT NOT NULL,
    full_name TEXT DEFAULT '',
    arc_number TEXT DEFAULT '',
    birth_date TEXT DEFAULT '',          -- YYMMDD
    nationality TEXT DEFAULT '',
    registered_address TEXT DEFAULT '',
    bank_name TEXT DEFAULT '',
    account_number TEXT DEFAULT '',
    folder_path TEXT DEFAULT '',
    status TEXT DEFAULT 'new',           -- new / review / ready
    warnings TEXT DEFAULT '[]',          -- JSON-список
    source_archive TEXT DEFAULT '',
    master_pdf TEXT DEFAULT '',
    printed INTEGER DEFAULT 0,
    phone TEXT DEFAULT '',
    email TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_clients_period ON clients(batch_period);

CREATE TABLE IF NOT EXISTS client_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    doc_type TEXT DEFAULT '',            -- code из doc_rules, '' = не определен
    file_path TEXT NOT NULL,             -- рабочий файл (PDF уже без пароля)
    original_name TEXT DEFAULT '',
    is_processed_scan INTEGER DEFAULT 0, -- 1 = файл создан редактором скана
    password_status TEXT DEFAULT '',     -- '' / unlocked / locked
    detected_by TEXT DEFAULT '',         -- как определен тип
    added_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_docs_client ON client_documents(client_id);

CREATE TABLE IF NOT EXISTS doc_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    doc_name TEXT NOT NULL,
    filename_patterns TEXT DEFAULT '[]', -- JSON
    ocr_keywords TEXT DEFAULT '[]',      -- JSON
    sort_order INTEGER DEFAULT 100,
    in_master INTEGER DEFAULT 1,
    print_step3 INTEGER DEFAULT 0,
    builtin INTEGER DEFAULT 0
);
"""

# Встроенные типы. id_a4 создается программой из лица ID.
BUILTIN_FRONT, BUILTIN_BACK, BUILTIN_BANK, BUILTIN_A4 = "id_front", "id_back", "bankbook", "id_a4"

DEFAULT_RULES = [
    # code, name, patterns, keywords, order, in_master, print, builtin
    (BUILTIN_A4, "ID лицо на А4", [], [], 1, 1, 1, 1),
    ("contract", "Договор", ["{ФИО}", "*계약*", "*contract*", "*dogovor*", "*договор*"],
     ["근로계약서", "표준근로계약서", "근로계약"], 2, 1, 1, 0),
    ("insurance", "Страховка", ["nhis*", "*보험*", "*insur*"], ["보험", "가입증명서"], 3, 1, 0, 0),
    ("employment", "Справка с работы", ["*재직*"], ["재직증명서"], 4, 1, 0, 0),
    ("income", "Справка о доходах", ["*소득*", "*income*"], ["소득금액증명", "원천징수"], 5, 1, 0, 0),
    ]
# Шаблоны по номеру файла и скриншоты (добавлены позже — старые базы получают их миграцией).
NEW_RULES_V2 = [
    ("sme_job", "중소기업취업", ["re:[1-9]([-_ ]\\d+)?"], [], 6, 1, 0, 0),
    ("global_income", "종합소득세", ["re:0[1-9]([-_ ]\\d+)?"], [], 7, 1, 0, 0),
    ("wage_statement", "근로소득지급명세서", ["re:2[0-9]([-_ ]\\d+)?"], [], 8, 1, 0, 0),
    ("screenshot", "Screenshot", ["Снимок экрана*", "Screenshot*", "스크린샷*", "화면 캡처*"], [], 9, 1, 0, 0),
]
DEFAULT_RULES = DEFAULT_RULES + NEW_RULES_V2 + [
    (BUILTIN_FRONT, "ID лицо (фото)", [], ["외국인등록증", "RESIDENCE CARD"], 101, 0, 0, 1),
    (BUILTIN_BACK, "ID оборот (фото)", [], ["체류지", "신고일"], 102, 0, 0, 1),
    (BUILTIN_BANK, "통장사본", ["*통장*", "*bank*"], ["은행", "계좌번호", "예금"], 103, 0, 0, 1),
]

CLIENT_FIELDS = ("full_name", "arc_number", "birth_date", "nationality", "registered_address",
                 "bank_name", "account_number", "folder_path", "status", "source_archive",
                 "master_pdf", "printed", "phone", "email")


@dataclass
class Client:
    id: int
    batch_period: str
    full_name: str = ""
    arc_number: str = ""
    birth_date: str = ""
    nationality: str = ""
    registered_address: str = ""
    bank_name: str = ""
    account_number: str = ""
    folder_path: str = ""
    status: str = "new"
    warnings: List[str] = field(default_factory=list)
    source_archive: str = ""
    master_pdf: str = ""
    printed: int = 0
    created_at: str = ""
    phone: str = ""
    email: str = ""

    @property
    def folder(self) -> Path:
        return Path(self.folder_path)


@dataclass
class Document:
    id: int
    client_id: int
    doc_type: str
    file_path: str
    original_name: str = ""
    is_processed_scan: int = 0
    password_status: str = ""
    detected_by: str = ""
    added_at: str = ""

    @property
    def path(self) -> Path:
        return Path(self.file_path)


@dataclass
class DocRule:
    id: int
    code: str
    doc_name: str
    filename_patterns: List[str]
    ocr_keywords: List[str]
    sort_order: int
    in_master: int
    print_step3: int
    builtin: int


class Database:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else config.db_path()
        with self.conn() as c:
            c.executescript(SCHEMA)
            # миграция старых баз: новые колонки
            cols = {r[1] for r in c.execute("PRAGMA table_info(clients)")}
            for col in ("phone", "email"):
                if col not in cols:
                    c.execute(f"ALTER TABLE clients ADD COLUMN {col} TEXT DEFAULT ''")
            if not c.execute("SELECT 1 FROM doc_rules LIMIT 1").fetchone():
                c.executemany(
                    "INSERT INTO doc_rules(code, doc_name, filename_patterns, ocr_keywords, sort_order,"
                    " in_master, print_step3, builtin) VALUES (?,?,?,?,?,?,?,?)",
                    [(r[0], r[1], json.dumps(r[2], ensure_ascii=False), json.dumps(r[3], ensure_ascii=False),
                      *r[4:]) for r in DEFAULT_RULES])
                c.execute("PRAGMA user_version = 2")
            if c.execute("PRAGMA user_version").fetchone()[0] < 2:
                self._migrate_rules_v2(c)
                c.execute("PRAGMA user_version = 2")

    @staticmethod
    def _migrate_rules_v2(c):
        """Старая база: добавить шаблоны «ФИО → договор», «nhis → страховка» и новые типы по номеру файла."""
        rows = {r["code"]: r for r in c.execute("SELECT code, filename_patterns FROM doc_rules")}
        for code, pat in (("contract", "{ФИО}"), ("insurance", "nhis*")):
            if code in rows:
                pats = json.loads(rows[code]["filename_patterns"] or "[]")
                if pat not in pats:
                    c.execute("UPDATE doc_rules SET filename_patterns=? WHERE code=?",
                              (json.dumps([pat] + pats, ensure_ascii=False), code))
        last = c.execute("SELECT COALESCE(MAX(sort_order), 0) FROM doc_rules WHERE sort_order < 100").fetchone()[0]
        for i, r in enumerate(NEW_RULES_V2, 1):
            if r[0] not in rows:
                c.execute("INSERT INTO doc_rules(code, doc_name, filename_patterns, ocr_keywords, sort_order,"
                          " in_master, print_step3, builtin) VALUES (?,?,?,?,?,?,?,?)",
                          (r[0], r[1], json.dumps(r[2], ensure_ascii=False), json.dumps(r[3], ensure_ascii=False),
                           last + i, *r[5:]))

    @contextmanager
    def conn(self):
        # Отдельное соединение на операцию: безопасно из фоновых потоков.
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        try:
            yield c
            c.commit()
        finally:
            c.close()

    # ------------------------------------------------------------ клиенты

    @staticmethod
    def _client(row) -> Client:
        d = dict(row)
        d["warnings"] = json.loads(d.get("warnings") or "[]")
        return Client(**d)

    def periods(self) -> List[str]:
        with self.conn() as c:
            return [r[0] for r in c.execute(
                "SELECT DISTINCT batch_period FROM clients ORDER BY batch_period DESC")]

    def clients(self, period: str) -> List[Client]:
        with self.conn() as c:
            rows = c.execute("SELECT * FROM clients WHERE batch_period=? ORDER BY id", (period,))
            return [self._client(r) for r in rows]

    def all_clients(self) -> List[Client]:
        with self.conn() as c:
            return [self._client(r) for r in c.execute("SELECT * FROM clients ORDER BY id")]

    def client(self, client_id: int) -> Optional[Client]:
        with self.conn() as c:
            row = c.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
            return self._client(row) if row else None

    def find_by_arc(self, period: str, arc: str) -> Optional[Client]:
        if not arc:
            return None
        with self.conn() as c:
            row = c.execute("SELECT * FROM clients WHERE batch_period=? AND arc_number=?",
                            (period, arc)).fetchone()
            return self._client(row) if row else None

    def clients_by_arc(self, arc: str) -> List[Client]:
        """Все клиенты с этим номером ARC во всех периодах (сравниваются только цифры)."""
        digits = "".join(ch for ch in arc if ch.isdigit())
        if len(digits) < 13:
            return []
        with self.conn() as c:
            rows = c.execute("SELECT * FROM clients WHERE replace(replace(arc_number,'-',''),' ','')=? "
                             "ORDER BY created_at DESC", (digits,))
            return [self._client(r) for r in rows]

    def add_client(self, period: str, **fields) -> int:
        warnings = fields.pop("warnings", [])
        created_at = fields.pop("created_at", None)
        if created_at:
            fields["created_at"] = created_at
        keys = [k for k in fields if k in CLIENT_FIELDS or k == "created_at"]
        with self.conn() as c:
            cur = c.execute(
                f"INSERT INTO clients(batch_period, warnings{''.join(', ' + k for k in keys)})"
                f" VALUES (?, ?{', ?' * len(keys)})",
                (period, json.dumps(warnings, ensure_ascii=False), *[fields[k] for k in keys]))
            return cur.lastrowid

    def update_client(self, client_id: int, **fields):
        sets, values = [], []
        for k, v in fields.items():
            if k == "warnings":
                sets.append("warnings=?")
                values.append(json.dumps(v, ensure_ascii=False))
            elif k in CLIENT_FIELDS:
                sets.append(f"{k}=?")
                values.append(v)
        if sets:
            with self.conn() as c:
                c.execute(f"UPDATE clients SET {', '.join(sets)} WHERE id=?", (*values, client_id))

    def delete_client(self, client_id: int):
        with self.conn() as c:
            c.execute("DELETE FROM clients WHERE id=?", (client_id,))

    # ------------------------------------------------------------ документы

    def documents(self, client_id: int) -> List[Document]:
        with self.conn() as c:
            rows = c.execute("SELECT * FROM client_documents WHERE client_id=? ORDER BY id", (client_id,))
            return [Document(**dict(r)) for r in rows]

    def add_document(self, client_id: int, doc_type: str, file_path, original_name: str = "",
                     is_processed_scan: int = 0, password_status: str = "", detected_by: str = "") -> int:
        with self.conn() as c:
            cur = c.execute(
                "INSERT INTO client_documents(client_id, doc_type, file_path, original_name,"
                " is_processed_scan, password_status, detected_by) VALUES (?,?,?,?,?,?,?)",
                (client_id, doc_type, str(file_path), original_name, is_processed_scan,
                 password_status, detected_by))
            return cur.lastrowid

    def update_document(self, doc_id: int, **fields):
        allowed = {"doc_type", "file_path", "is_processed_scan", "password_status", "detected_by"}
        items = [(k, v) for k, v in fields.items() if k in allowed]
        if items:
            with self.conn() as c:
                c.execute(f"UPDATE client_documents SET {', '.join(k + '=?' for k, _ in items)} WHERE id=?",
                          (*[str(v) if k == "file_path" else v for k, v in items], doc_id))

    def delete_document(self, doc_id: int):
        with self.conn() as c:
            c.execute("DELETE FROM client_documents WHERE id=?", (doc_id,))

    def documents_of_type(self, client_id: int, doc_type: str) -> List[Document]:
        return [d for d in self.documents(client_id) if d.doc_type == doc_type]

    # ------------------------------------------------------------ правила

    def rules(self) -> List[DocRule]:
        with self.conn() as c:
            rows = c.execute("SELECT * FROM doc_rules ORDER BY sort_order, id")
            out = []
            for r in rows:
                d = dict(r)
                d["filename_patterns"] = json.loads(d["filename_patterns"] or "[]")
                d["ocr_keywords"] = json.loads(d["ocr_keywords"] or "[]")
                out.append(DocRule(**d))
            return out

    def rule_names(self) -> dict:
        return {r.code: r.doc_name for r in self.rules()}

    def save_rule(self, rule: DocRule) -> int:
        data = (rule.doc_name, json.dumps(rule.filename_patterns, ensure_ascii=False),
                json.dumps(rule.ocr_keywords, ensure_ascii=False), rule.sort_order,
                int(rule.in_master), int(rule.print_step3))
        with self.conn() as c:
            if rule.id:
                c.execute("UPDATE doc_rules SET doc_name=?, filename_patterns=?, ocr_keywords=?,"
                          " sort_order=?, in_master=?, print_step3=? WHERE id=?", (*data, rule.id))
                return rule.id
            code = rule.code
            if not code:
                n = c.execute("SELECT COUNT(*) FROM doc_rules").fetchone()[0]
                code = f"custom_{n + 1}"
                while c.execute("SELECT 1 FROM doc_rules WHERE code=?", (code,)).fetchone():
                    n += 1
                    code = f"custom_{n + 1}"
            cur = c.execute("INSERT INTO doc_rules(code, doc_name, filename_patterns, ocr_keywords,"
                            " sort_order, in_master, print_step3, builtin) VALUES (?,?,?,?,?,?,?,0)",
                            (code, *data))
            return cur.lastrowid

    def delete_rule(self, rule_id: int):
        with self.conn() as c:
            row = c.execute("SELECT code, builtin FROM doc_rules WHERE id=?", (rule_id,)).fetchone()
            if not row or row["builtin"]:
                return
            c.execute("UPDATE client_documents SET doc_type='' WHERE doc_type=?", (row["code"],))
            c.execute("DELETE FROM doc_rules WHERE id=?", (rule_id,))

    def restore_default_rules(self) -> List[str]:
        """Возвращает удаленные стандартные типы и их маски/слова. Свое пользователя не трогает."""
        restored = []
        with self.conn() as c:
            rows = {r["code"]: r for r in c.execute("SELECT * FROM doc_rules")}
            last = c.execute("SELECT COALESCE(MAX(sort_order), 0) FROM doc_rules WHERE sort_order < 100").fetchone()[0]
            for code, name, pats, words, order, in_master, prn, builtin in DEFAULT_RULES:
                row = rows.get(code)
                if row is None:
                    last += 1
                    c.execute("INSERT INTO doc_rules(code, doc_name, filename_patterns, ocr_keywords, sort_order,"
                              " in_master, print_step3, builtin) VALUES (?,?,?,?,?,?,?,?)",
                              (code, name, json.dumps(pats, ensure_ascii=False), json.dumps(words, ensure_ascii=False),
                               order if builtin else last, in_master, prn, builtin))
                    restored.append(name)
                    continue
                cur_pats = json.loads(row["filename_patterns"] or "[]")
                cur_words = json.loads(row["ocr_keywords"] or "[]")
                add_pats = [x for x in pats if x not in cur_pats]
                add_words = [x for x in words if x not in cur_words]
                if add_pats or add_words:
                    c.execute("UPDATE doc_rules SET filename_patterns=?, ocr_keywords=? WHERE code=?",
                              (json.dumps(add_pats + cur_pats, ensure_ascii=False),
                               json.dumps(cur_words + add_words, ensure_ascii=False), code))
                    restored.append(f"{row['doc_name']}: " + ", ".join(add_pats + add_words))
        return restored

    def reorder_rules(self, ids_in_order: List[int]):
        with self.conn() as c:
            for i, rid in enumerate(ids_in_order, start=1):
                c.execute("UPDATE doc_rules SET sort_order=? WHERE id=?", (i, rid))
