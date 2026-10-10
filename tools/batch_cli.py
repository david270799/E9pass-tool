"""Пакетная обработка без окна: папка с фото -> Excel.

    python tools/batch_cli.py папка_с_фото результат.xlsx
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from e9pass_ocr.excel_export import card_to_row, save_xlsx  # noqa: E402
from e9pass_ocr.ocr_engine import IMAGE_EXTENSIONS, CardReader  # noqa: E402


def main():
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    files = sorted(p for p in src.iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS)
    reader = CardReader()
    rows = []
    for p in files:
        data, lines = reader.read(p)
        rows.append(card_to_row(p.name, data))
        print(f"{p.name}: {data.reg_no} | {data.name} | {data.country} | {data.visa} | "
              f"{data.issue_date} | {data.issuer} | {'; '.join(data.warnings)}")
    save_xlsx(rows, out)
    print(f"Сохранено: {out}")


if __name__ == "__main__":
    main()
