"""Пути программы. Все данные лежат в одной папке E9pass на этом компьютере."""

import os
import re
import sys
from pathlib import Path


def app_dir() -> Path:
    """Папка программы (рядом с .exe или корень репозитория)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def assets_dir() -> Path:
    base = Path(getattr(sys, "_MEIPASS", app_dir()))
    return base / "e9pass" / "assets"


def data_dir() -> Path:
    """Папка данных: E9PASS_HOME, иначе D:\\E9pass (если есть диск D), иначе ~/E9pass."""
    env = os.environ.get("E9PASS_HOME")
    if env:
        path = Path(env)
    elif sys.platform == "win32" and Path("D:/").exists():
        path = Path("D:/E9pass")
    else:
        path = Path.home() / "E9pass"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "e9pass.sqlite3"


def period_dir(period: str) -> Path:
    path = data_dir() / "periods" / period
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_name(text: str, fallback: str = "client") -> str:
    """Имя для папки/файла: без символов, запрещенных в Windows."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text).strip(" ._")
    name = re.sub(r"\s+", "_", name)
    return name[:80] or fallback


def unrar_tool() -> str:
    """UnRAR.exe рядом с программой (в папке tools), иначе системный unrar/7z/bsdtar."""
    for candidate in (app_dir() / "tools" / "UnRAR.exe", app_dir() / "tools" / "unrar"):
        if candidate.exists():
            return str(candidate)
    return ""
