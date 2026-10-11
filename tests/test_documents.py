"""Догрузка документов: файл без расширения, пароль из даты рождения, повторное определение типа."""

import pikepdf
import pymupdf
import pytest

from e9pass import services
from e9pass.db import Database


class NoOcr:
    def lines(self, img):
        return []

    def read_image_lines(self, path):
        return []


def make_pdf(path, text="hello", password=""):
    doc = pymupdf.open()
    doc.new_page().insert_text((60, 80), text)
    doc.save(path)
    doc.close()
    if password:
        with pikepdf.open(path, allow_overwriting_input=True) as pdf:
            pdf.save(path, encryption=pikepdf.Encryption(user=password, owner=password + "x"))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("E9PASS_HOME", str(tmp_path / "home"))
    db = Database(tmp_path / "db.sqlite3")
    folder = tmp_path / "home" / "periods" / "2026-08" / "client"
    folder.mkdir(parents=True)
    cid = db.add_client("2026-08", full_name="TEST", arc_number="950101-5123456", birth_date="950101",
                        folder_path=str(folder))
    return db, cid, tmp_path


def test_file_without_extension_is_unlocked_and_classified_after_rule_change(env):
    db, cid, tmp = env
    src = tmp / "nhis-20260730162847448hzr9ewQYqbX1b0T+"   # как скачивается с сайта — без .pdf
    make_pdf(src, "insurance", password="950101")
    services.add_documents(db, cid, [src], engine=NoOcr())
    doc = db.documents(cid)[0]
    assert doc.path.suffix == ".pdf"
    assert doc.password_status == "unlocked"
    assert doc.doc_type == ""                       # маски *nhis* еще нет

    rule = next(r for r in db.rules() if r.code == "insurance")
    rule.filename_patterns.append("*nhis*")
    db.save_rule(rule)
    assert services.recheck_all_unknown(db) == 1
    assert db.documents(cid)[0].doc_type == "insurance"


def test_old_document_without_extension_fixed_on_master_build(env):
    db, cid, tmp = env
    folder = db.client(cid).folder
    raw = folder / "nhis-abc"                       # добавлен старой версией, без расширения и с паролем
    make_pdf(raw, password="19950101")
    doc_id = db.add_document(cid, "insurance", raw, original_name="nhis-abc", password_status="locked")
    res = services.build_master(db, cid)
    doc = next(d for d in db.documents(cid) if d.id == doc_id)
    assert doc.path.suffix == ".pdf" and doc.password_status == "unlocked"
    assert res.path and "Страховка" in res.included and not res.locked


def test_wrong_password_reported(env):
    db, cid, tmp = env
    src = tmp / "secret.pdf"
    make_pdf(src, password="0000")
    services.add_documents(db, cid, [src], engine=NoOcr())
    rule_doc = db.documents(cid)[0]
    db.update_document(rule_doc.id, doc_type="contract")
    res = services.build_master(db, cid)
    assert res.locked == ["secret.pdf"]
    assert services.unlock_with_password(db, rule_doc.id, cid, "0000")


def test_mask_without_star_means_contains(env):
    db, cid, tmp = env
    rule = next(r for r in db.rules() if r.code == "insurance")
    rule.filename_patterns = ["NHIS"]
    code, _ = services.classify_file(tmp / "x.pdf", [rule], None, original_name="nhis-2026.pdf")
    assert code == "insurance"


def test_delete_client_removes_folder(env):
    db, cid, tmp = env
    folder = db.client(cid).folder
    (folder / "a.txt").write_text("x")
    services.delete_client_everything(db, cid)
    assert db.client(cid) is None and not folder.exists()


def test_registry_has_upper_and_lower_name(env, tmp_path):
    from openpyxl import load_workbook
    db, cid, tmp = env
    db.update_client(cid, full_name="EGAMBERDIEV AZAM- AT PULATOVICH")
    out = services.export_registry(db.clients("2026-08"), tmp / "r.xlsx")
    rows = list(load_workbook(out).active.iter_rows(values_only=True))
    assert rows[0][:3] == ("№", "ФИО (заглавными)", "ФИО (строчными)")
    assert rows[1][1:3] == ("EGAMBERDIEV AZAMAT PULATOVICH", "egamberdiev azamat pulatovich")
