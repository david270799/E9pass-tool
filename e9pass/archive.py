"""Распаковка .zip / .rar в папку клиента."""

import shutil
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import List

from . import config

ARCHIVE_EXTENSIONS = {".zip", ".rar"}
SKIP_NAMES = {"__MACOSX", ".DS_Store", "Thumbs.db", "desktop.ini"}


class ArchiveError(Exception):
    pass


def _zip_name(info: zipfile.ZipInfo) -> str:
    """Имена в zip из корейской Windows часто в cp949, а не UTF-8."""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        raw = info.filename.encode("cp437")
    except UnicodeEncodeError:
        return info.filename
    for enc in ("utf-8", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return info.filename


def _safe_target(dest: Path, name: str) -> Path:
    """Защита от путей вида ../../ в архиве."""
    parts = [p for p in PurePosixPath(name.replace("\\", "/")).parts if p not in ("", ".", "..", "/")]
    target = dest.joinpath(*parts) if parts else dest
    if dest.resolve() not in target.resolve().parents and target.resolve() != dest.resolve():
        raise ArchiveError(f"Недопустимый путь в архиве: {name}")
    return target


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 1000):
        cand = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not cand.exists():
            return cand
    raise ArchiveError(f"Слишком много файлов с именем {path.name}")


def extract(archive: Path, dest: Path) -> List[Path]:
    """Распаковывает архив в dest (плоско, без вложенных папок). Возвращает список файлов."""
    archive, dest = Path(archive), Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    out: List[Path] = []
    ext = archive.suffix.lower()
    try:
        if ext == ".zip":
            with zipfile.ZipFile(archive) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    name = _zip_name(info)
                    base = PurePosixPath(name.replace("\\", "/"))
                    if any(p in SKIP_NAMES or p.startswith("._") for p in base.parts):
                        continue
                    _safe_target(dest, name)
                    target = _unique(dest / (config.safe_name(base.stem, "file") + base.suffix.lower()))
                    with zf.open(info) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    out.append(target)
        elif ext == ".rar":
            import rarfile

            tool = config.unrar_tool()
            if tool:
                rarfile.UNRAR_TOOL = tool
            elif sys.platform == "win32":
                rarfile.BSDTAR_TOOL = "tar"  # tar.exe встроен в Windows 10/11 и читает RAR
            with rarfile.RarFile(archive) as rf:
                for info in rf.infolist():
                    if info.is_dir():
                        continue
                    base = PurePosixPath(info.filename.replace("\\", "/"))
                    if any(p in SKIP_NAMES for p in base.parts):
                        continue
                    _safe_target(dest, info.filename)
                    target = _unique(dest / (config.safe_name(base.stem, "file") + base.suffix.lower()))
                    with rf.open(info) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    out.append(target)
        else:
            raise ArchiveError(f"Неподдерживаемый формат: {archive.name}")
    except ArchiveError:
        raise
    except zipfile.BadZipFile as e:
        raise ArchiveError(f"Архив поврежден: {archive.name}") from e
    except Exception as e:  # rarfile: нет UnRAR, пароль, повреждение
        msg = str(e)
        if "unrar" in msg.lower() or "Cannot find working tool" in msg:
            msg = "Не удалось распаковать RAR: положите UnRAR.exe в папку tools рядом с программой"
        raise ArchiveError(f"{archive.name}: {msg}") from e
    return out
