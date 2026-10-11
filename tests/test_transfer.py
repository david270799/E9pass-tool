"""Передача клиентов файлом .e9pay и проверка повторов по ARC (меньше года — ошибка)."""

import pytest

from e9pass import services, transfer
from e9pass.db import Database, DocRule


@pytest.fixture
def two_pcs(tmp_path, monkeypatch):
    monkeypatch.setenv("E9PASS_HOME", str(tmp_path / "home"))
    a = Database(tmp_path / "a.sqlite3")
    b = Database(tmp_path / "b.sqlite3")
    folder = tmp_path / "home" / "periods" / "2026-08" / "NGUYEN"
    (folder / "originals").mkdir(parents=True)
    (folder / "scans").mkdir()
    (folder / "originals" / "front.jpg").write_bytes(b"\xff\xd8\xff fake")
    (folder / "originals" / "nhis.pdf").write_bytes(b"%PDF-1.4 fake")
    (folder / "scans" / "ID_front_scan.png").write_bytes(b"\x89PNG fake")
    cid = a.add_client("2026-08", full_name="NGUYEN VAN AN", arc_number="950101-5123456", birth_date="950101",
                       nationality="VIETNAM", registered_address="경기도 화성시", bank_name="KB국민은행",
                       account_number="123-456", folder_path=str(folder), status="ready")
    rid = a.save_rule(DocRule(0, "", "Страховка NHIS", ["*nhis*"], ["건강보험"], 6, 1, 0, 0))
    code = next(r.code for r in a.rules() if r.id == rid)
    a.add_document(cid, "id_front", folder / "originals" / "front.jpg", original_name="IMG_1.jpg")
    a.add_document(cid, code, folder / "originals" / "nhis.pdf", original_name="nhis-2026",
                   password_status="unlocked", detected_by="маска")
    return a, b, cid, tmp_path


def test_export_import_keeps_everything_without_ocr(two_pcs):
    a, b, cid, tmp = two_pcs
    pkg_path = transfer.export_clients(a, [cid], tmp / "out")
    assert pkg_path.suffix == ".e9pay"
    pkg = transfer.read_package(b, pkg_path)
    assert len(pkg.clients) == 1 and pkg.clients[0].action == transfer.ACTION_NEW
    ids = transfer.import_package(b, pkg)
    c = b.client(ids[0])
    assert (c.full_name, c.arc_number, c.registered_address, c.bank_name, c.status) == \
           ("NGUYEN VAN AN", "950101-5123456", "경기도 화성시", "KB국민은행", "ready")
    docs = b.documents(c.id)
    assert {d.original_name for d in docs} == {"IMG_1.jpg", "nhis-2026"}
    nhis = next(d for d in docs if d.original_name == "nhis-2026")
    rule = next(r for r in b.rules() if r.code == nhis.doc_type)
    assert rule.doc_name == "Страховка NHIS" and rule.filename_patterns == ["*nhis*"]   # тип добавлен
    assert (c.folder / "scans" / "ID_front_scan.png").exists()                       # скан тоже перенесен
    assert c.created_at == a.client(cid).created_at                                  # дата добавления сохранена


def test_second_import_is_flagged_as_duplicate(two_pcs):
    a, b, cid, tmp = two_pcs
    p = transfer.export_clients(a, [cid], tmp / "out.e9pay")
    transfer.import_package(b, transfer.read_package(b, p))
    again = transfer.read_package(b, p)
    assert again.clients[0].duplicates and again.clients[0].action == transfer.ACTION_REPLACE
    again.clients[0].action = transfer.ACTION_SKIP
    transfer.import_package(b, again)
    assert len(b.clients_by_arc("950101-5123456")) == 1


def test_password(two_pcs):
    a, b, cid, tmp = two_pcs
    p = transfer.export_clients(a, [cid], tmp / "secret.e9pay", password="1234")
    with pytest.raises(transfer.PasswordNeeded):
        transfer.read_package(b, p)
    with pytest.raises(transfer.PasswordNeeded):
        transfer.read_package(b, p, "0000")
    assert transfer.import_package(b, transfer.read_package(b, p, "1234"))


def test_duplicate_older_than_a_year_is_allowed(two_pcs):
    a, b, cid, tmp = two_pcs
    recent, old = services.recent_duplicates(a, "9501015123456")
    assert [c.id for c in recent] == [cid] and old == []
    with a.conn() as conn:
        conn.execute("UPDATE clients SET created_at='2024-01-01 10:00:00' WHERE id=?", (cid,))
    recent, old = services.recent_duplicates(a, "950101-5123456")
    assert recent == [] and [c.id for c in old] == [cid]
