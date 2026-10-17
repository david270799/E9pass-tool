"""Тип документа по имени файла: номера, nhis, скриншоты, ФИО клиента."""

import sqlite3

from e9pass import services
from e9pass.db import NEW_RULES_V2, Database


def classify(db, name, client="KARIMOV JASUR BAKHTIYOR UGLI"):
    return services.classify_file(__import__("pathlib").Path(name), db.rules(), None, name, client)[0]


def test_number_and_name_templates(tmp_path):
    db = Database(tmp_path / "db.sqlite3")
    for n in ["1.pdf", "3.pdf", "5.pdf"]:
        assert classify(db, n) == "sme_job", n
    for n in ["01.pdf", "02.pdf", "05.pdf"]:
        assert classify(db, n) == "global_income", n
    for n in ["21.pdf", "24.pdf", "26.pdf", "21-1.pdf", "23-1.pdf"]:
        assert classify(db, n) == "wage_statement", n
    assert classify(db, "nhis-20261017184735.pdf") == "insurance"
    for n in ["Снимок экрана 2026-10-17 184512.png", "Screenshot 2026-10-17 184512.png",
              "스크린샷 2026-10-17 184512.png"]:
        assert classify(db, n) == "screenshot", n
    for n in ["KARIMOV JASUR.pdf", "karimov jasur.pdf", "KARIMOV JASUR (2).pdf", "Jasur Karimov.pdf"]:
        assert classify(db, n) == "contract", n
    assert classify(db, "ALIEV RUSTAM.pdf") == ""      # чужое ФИО — не договор этого клиента
    assert classify(db, "123.pdf") == ""


def test_master_order_is_numeric(tmp_path):
    keys = sorted(["22", "21-1", "3", "21", "26", "21-2", "1", "10"], key=services._natural_key)
    assert keys == ["1", "3", "10", "21", "21-1", "21-2", "22", "26"]


def test_old_database_gets_new_rules(tmp_path):
    path = tmp_path / "old.sqlite3"
    Database(path)
    with sqlite3.connect(path) as c:  # имитируем старую базу: без новых типов и шаблонов
        c.execute("DELETE FROM doc_rules WHERE code IN (%s)" % ",".join("?" * len(NEW_RULES_V2)),
                  [r[0] for r in NEW_RULES_V2])
        c.execute("""UPDATE doc_rules SET filename_patterns='["*보험*"]' WHERE code='insurance'""")
        c.execute("PRAGMA user_version = 0")
    db = Database(path)
    codes = {r.code: r for r in db.rules()}
    assert all(r[0] in codes for r in NEW_RULES_V2)
    assert codes["insurance"].filename_patterns[0] == "nhis*"
    assert "{ФИО}" in codes["contract"].filename_patterns
    Database(path)  # повторное открытие ничего не дублирует
    assert len(db.rules()) == len(codes)
