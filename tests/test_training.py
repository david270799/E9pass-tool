"""Учебная база OCR и словарь имен."""

from PIL import Image

from e9pass import training
from e9pass.db import Database


def make_photo(path, color="white"):
    Image.new("RGB", (400, 250), color).save(path)
    return path


def test_store_add_label_and_dedupe(tmp_path):
    store = training.TrainingStore(tmp_path / "t" / "training.sqlite3")
    a = make_photo(tmp_path / "a.jpg")
    b = make_photo(tmp_path / "b.jpg", "gray")
    ids = store.add_images([a, b, a, tmp_path / "notes.txt"])
    assert len(ids) == 2                                  # повтор и не-фото не добавлены
    assert all(c.status == training.NEW for c in store.cards())
    store.set_prediction(ids[0], training.FRONT, name="KARIMOV JASUP", arc="900101-5123452", country="UZBEKISTAN")
    c = store.card(ids[0])
    assert c.status == training.READY and c.pred_name == "KARIMOV JASUP" and c.name == "KARIMOV JASUP"
    store.save_label(ids[0], training.FRONT, name="KARIMOV JASUR", arc="900101-5123452", country="UZBEKISTAN")
    store.save_label(ids[1], training.OTHER)
    st = store.stats()
    assert st[training.DONE] == 1 and st["front"] == 1 and st[training.SKIP] == 1
    store.delete(ids[1])
    assert len(store.cards()) == 1


def test_import_clients(tmp_path, monkeypatch):
    monkeypatch.setenv("E9PASS_HOME", str(tmp_path / "home"))
    db = Database(tmp_path / "db.sqlite3")
    cid = db.add_client("2026-10", full_name="KARIMOV JASUR", arc_number="900101-5123452", nationality="UZBEKISTAN",
                        registered_address="경기도 수원시 팔달구 효원로 1", folder_path=str(tmp_path))
    db.add_document(cid, "id_front", make_photo(tmp_path / "f.jpg"))
    db.add_document(cid, "id_back", make_photo(tmp_path / "b.jpg", "gray"))
    db.add_document(cid, "id_a4", make_photo(tmp_path / "a4.jpg", "red"), is_processed_scan=1)
    store = training.TrainingStore(tmp_path / "t" / "training.sqlite3")
    assert store.import_clients(db) == 2
    assert store.import_clients(db) == 0                  # второй раз ничего не дублирует
    sides = {c.side: c for c in store.cards()}
    assert sides["front"].name == "KARIMOV JASUR" and sides["back"].address.startswith("경기도")


def test_dictionary_correction():
    words = {"KAKHRAMONOV", "IBRATBEK", "IBROKHIMJON", "UGLI", "ABDURAKHMONOVICH", "JASUR"}
    assert training.correct_with_dictionary("KAKHRAMONOV IBRATBFK IBROKHIMJON UGLI", words) == \
        "KAKHRAMONOV IBRATBEK IBROKHIMJON UGLI"
    assert training.correct_with_dictionary("ABDURAKHMQNQVICH", words) == "ABDURAKHMONOVICH"  # 2 ошибки в длинном
    assert training.correct_with_dictionary("JASUP", words) == "JASUP"          # короткие слова не трогаем
    assert training.correct_with_dictionary("SHERZOD", words) == "SHERZOD"      # нет похожих — как есть


def test_match_segment_handles_wraps():
    label = "KAKHRAMONOV IBRATBEK IBROKHIMJON UGLI"
    assert training.match_segment("KAKHRAMONOV IBRA-", label) == "KAKHRAMONOV IBRA-"
    assert training.match_segment("TBEK IBROKHIMJON", label) == "TBEK IBROKHIMJON"
    assert training.match_segment("UGL", label) == "UGLI"
    assert training.match_segment("IRKINOVASILBEK", "IRKINOV ASILBEK BAHTIYOR UGLI") == "IRKINOV ASILBEK"
    assert training.match_segment("UZBEKISTAN", label) == ""
    addr = "경상남도 밀양시 하남읍 시동3리2길 9-1, 504호"
    assert training.match_segment("경상남도 밀양시 하남읍 시동3리2", addr) == "경상남도 밀양시 하남읍 시동3리2"
    assert training.match_segment("길9-1.504호", addr) == "길 9-1, 504호"


def test_regions_saved_with_label_and_migrated(tmp_path):
    import sqlite3
    path = tmp_path / "t" / "training.sqlite3"
    store = training.TrainingStore(path)
    cid = store.add_image(make_photo(tmp_path / "a.jpg"))
    store.set_prediction(cid, training.FRONT, name="KARIMOV JASUR", rotation=90,
                         regions={"name": [0.2, 0.4, 0.5, 0.5]})
    assert store.card(cid).rotation == 90 and store.card(cid).region_map == {"name": [0.2, 0.4, 0.5, 0.5]}
    store.save_label(cid, training.FRONT, name="KARIMOV JASUR", rotation=180,
                     regions={"name": [0.1, 0.4, 0.6, 0.55], "arc": [0.1, 0.3, 0.5, 0.35]})
    c = store.card(cid)
    assert c.rotation == 180 and set(c.region_map) == {"name", "arc"}
    # старая база без колонок rotation / regions получает их при открытии
    old = tmp_path / "old.sqlite3"
    with sqlite3.connect(old) as con:
        con.execute("CREATE TABLE cards (id INTEGER PRIMARY KEY AUTOINCREMENT, image TEXT NOT NULL, image_hash TEXT"
                    " UNIQUE, original_name TEXT DEFAULT '', side TEXT DEFAULT '', status TEXT DEFAULT 'new',"
                    " name TEXT DEFAULT '', arc TEXT DEFAULT '', country TEXT DEFAULT '', address TEXT DEFAULT '',"
                    " pred_name TEXT DEFAULT '', pred_arc TEXT DEFAULT '', pred_country TEXT DEFAULT '',"
                    " pred_address TEXT DEFAULT '', source TEXT DEFAULT 'photo', added_at TEXT DEFAULT '',"
                    " checked_at TEXT DEFAULT '')")
    s2 = training.TrainingStore(old)
    s2.add_image(make_photo(tmp_path / "b.jpg", "gray"))
    assert s2.cards()[0].region_map == {}
