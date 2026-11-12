"""Общее состояние окна: база, выбранный период, сигналы между экранами."""

from datetime import date

from PySide6.QtCore import QObject, Signal

from ..db import Database


def current_period() -> str:
    return date.today().strftime("%Y-%m")


def shift_period(period: str, months: int) -> str:
    y, m = map(int, period.split("-"))
    m += months
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f"{y:04d}-{m:02d}"


def inbox_folder() -> str:
    """Общая папка, откуда кнопка «Забрать документы» забирает файлы в карточку клиента (Настройки)."""
    from PySide6.QtCore import QSettings
    return QSettings("E9pay", "E9pay").value("inbox_folder", "", type=str)


def set_inbox_folder(path: str):
    from PySide6.QtCore import QSettings
    QSettings("E9pay", "E9pay").setValue("inbox_folder", path)


def _settings():
    from PySide6.QtCore import QSettings
    return QSettings("E9pay", "E9pay")


def sync_folder() -> str:
    """Общая папка на сервере для синхронизации между компьютерами ('' — синхронизация выключена)."""
    return _settings().value("sync_folder", "", type=str)


def sync_auto() -> bool:
    return _settings().value("sync_auto", True, type=bool)


def computer_label() -> str:
    return _settings().value("computer_name", "", type=str)


def save_sync_settings(folder: str, name: str, auto: bool):
    from .. import config
    s = _settings()
    s.setValue("sync_folder", folder)
    s.setValue("computer_name", name)
    s.setValue("sync_auto", auto)
    config.set_computer_name(name)


class AppState(QObject):
    period_changed = Signal(str)
    data_changed = Signal()                # клиенты/документы изменились
    open_client = Signal(int)
    navigate = Signal(str)                 # имя экрана
    archives_dropped = Signal(list)        # новые архивы на приемку
    busy_changed = Signal(str)             # текст статуса фоновой работы ('' — свободно)
    sync_requested = Signal(bool)          # «Обновить»: True — вручную (с итогом), False — авто
    sync_status = Signal(str)              # строка состояния синхронизации

    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        # месяц по дате Windows; при смене месяца переключается сам (см. MainWindow)
        self.today_period = current_period()
        self.period = self.today_period

    def follow_calendar(self):
        """Наступил новый месяц: если пользователь смотрел «текущий» месяц — перейти на новый."""
        now = current_period()
        if now != self.today_period:
            was_current = self.period == self.today_period
            self.today_period = now
            if was_current:
                self.set_period(now)

    def set_period(self, period: str):
        if period != self.period:
            self.period = period
            self.period_changed.emit(period)
