"""Запуск программы: python main.py (самопроверка сборки: --selftest)"""

import os
import sys


def _setup_streams():
    # В собранном .exe без консоли sys.stdout/stderr = None — библиотеки падают при записи.
    # Ошибки пишем в журнал в папке данных.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        try:
            from e9pass import config
            sys.stderr = open(config.data_dir() / "e9pay.log", "a", encoding="utf-8", buffering=1)
        except OSError:
            sys.stderr = open(os.devnull, "w", encoding="utf-8")


if __name__ == "__main__":
    _setup_streams()
    if "--selftest" in sys.argv:
        from e9pass.selftest import run
        sys.exit(run())
    from e9pass.ui.main_window import main
    main()
