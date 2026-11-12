"""Синхронизация двух компьютеров через общую папку (два «компьютера» — две базы и две папки данных)."""

import time

import pytest

from e9pass import config, services, sync
from e9pass.db import Database


class PC:
    def __init__(self, tmp_path, monkeypatch, name):
        self.name, self.home, self.mp = name, tmp_path / name, monkeypatch
        self.home.mkdir(parents=True)
        self.use()
        self.db = Database(self.home / "db.sqlite3")

    def use(self):
        self.mp.setenv("E9PASS_HOME", str(self.home))
        config.set_computer_name(self.name)

    def sync(self, shared):
        self.use()
        self.db.computer = self.name
        time.sleep(0.01)
        return sync.synchronize(self.db, shared)

    def add(self, name, arc, period="2026-11", **kw):
        self.use()
        folder = config.period_dir(period) / name.replace(" ", "_")
        (folder / "originals").mkdir(parents=True)
        return self.db.add_client(period, full_name=name, arc_number=arc, folder_path=str(folder), **kw)

    def add_file(self, cid, name, data=b"%PDF-1.4 test"):
        c = self.db.client(cid)
        f = c.folder / "originals" / name
        f.write_bytes(data)
        return self.db.add_document(cid, "insurance", f, original_name=name)

    def by_arc(self, arc):
        return next((c for c in self.db.all_clients() if c.arc_number == arc), None)


@pytest.fixture
def two(tmp_path, monkeypatch):
    shared = tmp_path / "server_share"
    shared.mkdir()
    yield PC(tmp_path, monkeypatch, "PC-1"), PC(tmp_path, monkeypatch, "PC-2"), shared
    config.set_computer_name("")


def test_new_client_and_files_reach_other_computer(two):
    a, b, shared = two
    cid = a.add("KARIMOV JASUR", "900101-5123456", phone="010-1111-2222")
    a.add_file(cid, "nhis.pdf")
    r = a.sync(shared)
    assert r.sent_clients == 1 and r.sent_files == 1
    r = b.sync(shared)
    assert r.new_clients == ["KARIMOV JASUR"] and r.received_files == 1
    c = b.by_arc("900101-5123456")
    assert c.phone == "010-1111-2222" and c.batch_period == "2026-11"
    docs = b.db.documents(c.id)
    assert len(docs) == 1 and docs[0].path.exists() and str(b.home) in str(docs[0].path)
    a.sync(shared)
    assert sorted(sync.presence(a.db)[c.uid]) == ["PC-1", "PC-2"]      # 🌐 есть у всех


def test_edit_travels_and_later_change_wins(two):
    a, b, shared = two
    a.add("KARIMOV JASUR", "900101-5123456")
    a.sync(shared)
    b.sync(shared)
    cb = b.by_arc("900101-5123456")
    b.use()
    services.set_client_fields(b.db, cb.id, phone="010-2222-3333")
    b.sync(shared)
    r = a.sync(shared)
    assert a.by_arc("900101-5123456").phone == "010-2222-3333" and r.updated_clients == ["KARIMOV JASUR"]
    # одно поле на обоих: побеждает более позднее изменение, конфликт виден в итоге
    a.use()
    services.set_client_fields(a.db, a.by_arc("900101-5123456").id, phone="010-4444-5555")
    time.sleep(0.01)
    b.use()
    services.set_client_fields(b.db, cb.id, phone="010-6666-7777")
    a.sync(shared)
    r = b.sync(shared)
    assert b.by_arc("900101-5123456").phone == "010-6666-7777"
    assert r.conflicts and "оставлено «010-6666-7777»" in r.conflicts[0]
    a.sync(shared)
    assert a.by_arc("900101-5123456").phone == "010-6666-7777"


def test_delete_is_local_only_and_does_not_come_back(two):
    a, b, shared = two
    cid = a.add("KARIMOV JASUR", "900101-5123456")
    a.sync(shared)
    b.sync(shared)
    b.use()
    services.delete_client_everything(b.db, b.by_arc("900101-5123456").id)
    b.sync(shared)
    a.sync(shared)
    assert a.db.client(cid) is not None                                  # у другого остался
    assert sync.presence(a.db)[a.db.client(cid).uid] == ["PC-1"]         # 🖥 только у него
    b.sync(shared)
    assert b.by_arc("900101-5123456") is None                            # и не вернулся


def test_removed_document_stays_removed_here(two):
    a, b, shared = two
    cid = a.add("KARIMOV JASUR", "900101-5123456")
    a.add_file(cid, "nhis.pdf")
    a.sync(shared)
    b.sync(shared)
    cb = b.by_arc("900101-5123456")
    b.db.delete_document(b.db.documents(cb.id)[0].id)
    b.sync(shared)
    a.sync(shared)
    b.sync(shared)
    assert b.db.documents(cb.id) == [] and len(a.db.documents(cid)) == 1


def test_same_archive_on_both_computers_becomes_one_client(two):
    a, b, shared = two
    a.add("KARIMOV JASUR", "900101-5123456")
    b.add("KARIMOV JASUR", "900101-5123456", phone="010-9999-8888")
    a.sync(shared)
    b.sync(shared)
    a.sync(shared)
    assert len(a.db.all_clients()) == 1 and len(b.db.all_clients()) == 1
    assert a.by_arc("900101-5123456").phone == "010-9999-8888"


def test_month_move_travels_with_files(two):
    a, b, shared = two
    cid = a.add("KARIMOV JASUR", "900101-5123456")
    a.add_file(cid, "nhis.pdf")
    a.sync(shared)
    b.sync(shared)
    a.use()
    services.move_clients(a.db, [cid], "2026-12")
    a.sync(shared)
    b.sync(shared)
    c = b.by_arc("900101-5123456")
    assert c.batch_period == "2026-12" and c.folder.parent.name == "2026-12"
    assert all(d.path.exists() for d in b.db.documents(c.id))


def test_unavailable_share_is_reported(tmp_path, monkeypatch):
    pc = PC(tmp_path, monkeypatch, "PC-1")
    with pytest.raises(sync.SyncError):
        pc.sync(tmp_path / "no_such_server")
    config.set_computer_name("")


def test_trained_model_and_name_dictionary_reach_other_computer(two):
    import json
    from e9pass import ocr_trainer, training
    a, b, shared = two
    a.use()
    act = ocr_trainer.active_dir()
    act.mkdir(parents=True, exist_ok=True)
    (act / "latin.onnx").write_bytes(b"model-v1")
    (act / "history.json").write_text(json.dumps({"latin": {"date": "2026-11-12T10:00:00", "before": 0.8,
                                                            "after": 0.96, "computer": "PC-1"}}), encoding="utf-8")
    a.sync(shared)
    r = b.sync(shared)
    b.use()
    assert (ocr_trainer.active_dir() / "latin.onnx").read_bytes() == b"model-v1"
    assert ocr_trainer.history()["latin"]["computer"] == "PC-1" and r.models
    assert b.sync(shared).models == []                                  # повторно не копируется
