"""Самопроверка собранной программы: E9pay.exe --selftest

Проверяет, что в сборке есть всё нужное: OCR и модели, PDF, Excel, окно.
Результат — код выхода (0 = ОК) и файл e9pay_selftest.log во временной папке.
"""

import os
import sys
import tempfile
import traceback
from pathlib import Path


def run() -> int:
    log_path = Path(tempfile.gettempdir()) / "e9pay_selftest.log"
    lines = []

    def log(msg):
        lines.append(msg)
        log_path.write_text("\n".join(lines), encoding="utf-8")

    try:
        home = Path(tempfile.mkdtemp(prefix="e9pay_selftest_"))
        os.environ["E9PASS_HOME"] = str(home)
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from . import config
        log(f"app_dir={config.app_dir()} assets={config.assets_dir()}")

        from .ocr.ocr_engine import MODEL_FILES, OcrEngine, models_dir
        missing = [f for f in MODEL_FILES if not (models_dir() / f).exists()]
        if missing:
            raise RuntimeError(f"нет моделей OCR: {missing} в {models_dir()}")
        sample = config.app_dir() / "selftest" / "sample_front.jpg"
        if not sample.exists():
            sample = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "sample_front.jpg"
        card, ocr_lines = OcrEngine.get().read_front(sample)
        log(f"OCR: {card.reg_no} | {card.name} | {card.country}")
        if card.reg_no != "950101-5123456":
            raise RuntimeError(f"OCR вернул неверный номер: {card.reg_no!r}; строки: {ocr_lines}")

        import pikepdf  # noqa: F401
        import pymupdf
        from . import scan, services
        from .db import Database
        db = Database()
        cid = db.add_client("2026-01", full_name=card.name, arc_number=card.reg_no, birth_date="950101",
                            folder_path=str(home / "c1"))
        (home / "c1").mkdir()
        pdf = services.make_id_a4(db, cid, sample)
        with pymupdf.open(pdf) as d:
            log(f"A4 PDF: {d.page_count} стр.")
        services.export_registry(db.clients("2026-01"), home / "r.xlsx")
        log("Excel: ок")
        _ = scan.FILTER_CLEAN

        from PySide6.QtWidgets import QApplication
        from .ui import theme
        from .ui.main_window import MainWindow
        app = QApplication.instance() or QApplication(sys.argv[:1])
        app.setStyleSheet(theme.QSS)
        win = MainWindow(db)
        win.show()
        app.processEvents()
        win.close()
        log("UI: ок")
        log("SELFTEST OK")
        return 0
    except Exception:  # noqa: BLE001
        log("SELFTEST FAILED\n" + traceback.format_exc())
        return 1
