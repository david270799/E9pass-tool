"""Распаковка RAR: архивы-образцы из тестов rarfile (ISC, Marko Kreen)."""

from pathlib import Path

import pytest

from e9pass import archive

FIX = Path(__file__).parent / "fixtures"


def _has_tool():
    return bool(archive._rar_commands(FIX / "rar5-solid.rar"))


@pytest.mark.skipif(not _has_tool(), reason="нет tar/7z/UnRAR")
def test_rar5_solid_extracts_whole(tmp_path, monkeypatch):
    # Потоковое чтение через tar падало на таких архивах: «Failed the read enough data».
    monkeypatch.setattr(archive, "_extract_rar_stream",
                        lambda *a: pytest.fail("не должно доходить до rarfile"))
    files = archive.extract(FIX / "rar5-solid.rar", tmp_path / "out")
    assert sorted(f.name for f in files) == ["stest1.txt", "stest2.txt"]
    assert all(f.parent == tmp_path / "out" and f.stat().st_size > 0 for f in files)


@pytest.mark.skipif(not _has_tool(), reason="нет tar/7z/UnRAR")
def test_rar_subfolders_are_flattened(tmp_path):
    files = archive.extract(FIX / "rar3-subdirs.rar", tmp_path / "out")
    assert files and all(f.parent == tmp_path / "out" for f in files)
    assert len({f.name for f in files}) == len(files)


def test_rar_failure_gives_clear_message(tmp_path, monkeypatch):
    bad = tmp_path / "client.rar"
    bad.write_bytes(b"Rar!\x1a\x07\x01\x00garbage")
    monkeypatch.setattr(archive, "_rar_commands", lambda a: [])
    with pytest.raises(archive.ArchiveError) as e:
        archive.extract(bad, tmp_path / "out")
    assert "client.rar" in str(e.value) and "zip" in str(e.value)
