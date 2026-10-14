"""Распаковка .zip / .rar в папку клиента."""

import os
import shutil
import subprocess
import sys
import tempfile
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


RAR_HELP = ("не удалось распаковать RAR. Пересохраните архив в .zip или положите 7z.exe + 7z.dll "
            "(7-Zip) либо UnRAR.exe в папку tools рядом с программой")


OUT = "{out}"  # заменяется на временную папку


def _rar_commands(archive: Path) -> List[List[str]]:
    """Внешние распаковщики по очереди: UnRAR.exe → 7-Zip → tar.exe (Windows 10/11)."""
    cmds: List[List[str]] = []
    unrar = config.unrar_tool()
    if unrar:
        cmds.append([unrar, "x", "-y", "-p-", "-idq", str(archive), OUT + os.sep])
    sevens = [config.app_dir() / "tools" / "7z.exe"]
    for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        if os.environ.get(env):
            sevens.append(Path(os.environ[env]) / "7-Zip" / "7z.exe")
    found = [str(p) for p in sevens if p.exists()]
    for name in ("7z", "7za"):
        w = shutil.which(name)
        if w:
            found.append(w)
    for exe in dict.fromkeys(found):
        cmds.append([exe, "x", "-y", "-bd", "-p-", "-o" + OUT, str(archive)])
    tar = shutil.which("bsdtar") or shutil.which("tar")
    if tar:
        cmds.append([tar, "-xf", str(archive), "-C", OUT])
    return cmds


def _run_quiet(cmd: List[str]) -> bool:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=600, creationflags=flags)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def _collect(src_root: Path, dest: Path, out: List[Path]) -> None:
    """Переносит распакованные файлы из временной папки в dest плоским списком."""
    root = src_root.resolve()
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_NAMES)
        for fn in sorted(filenames):
            path = Path(dirpath) / fn
            if fn in SKIP_NAMES or fn.startswith("._") or path.is_symlink() or not path.is_file():
                continue
            if root not in path.resolve().parents:
                continue
            files.append(path)
    for path in files:
        target = _unique(dest / (config.safe_name(path.stem, "file") + path.suffix.lower()))
        shutil.copyfile(path, target)
        out.append(target)


def _extract_rar(archive: Path, dest: Path, out: List[Path]) -> None:
    # Целиком во временную папку внешним распаковщиком: потоковое чтение rarfile через
    # tar.exe ломается на RAR5 и solid-архивах («Failed the read enough data»).
    for template in _rar_commands(archive):
        with tempfile.TemporaryDirectory(prefix="e9rar_") as tmp:
            if _run_quiet([a.replace(OUT, tmp) for a in template]):
                before = len(out)
                _collect(Path(tmp), dest, out)
                if len(out) > before:
                    return
    _extract_rar_stream(archive, dest, out)


def _extract_rar_stream(archive: Path, dest: Path, out: List[Path]) -> None:
    import rarfile

    tool = config.unrar_tool()
    if tool:
        rarfile.UNRAR_TOOL = tool
    elif sys.platform == "win32":
        rarfile.BSDTAR_TOOL = "tar"
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
            _extract_rar(archive, dest, out)
            if not out:
                raise ArchiveError(f"{archive.name}: {RAR_HELP}")
        else:
            raise ArchiveError(f"Неподдерживаемый формат: {archive.name}")
    except ArchiveError:
        raise
    except zipfile.BadZipFile as e:
        raise ArchiveError(f"Архив поврежден: {archive.name}") from e
    except Exception as e:  # rarfile: нет UnRAR, пароль, повреждение
        msg = str(e)
        if ext == ".rar":
            msg = RAR_HELP
        raise ArchiveError(f"{archive.name}: {msg}") from e
    return out
