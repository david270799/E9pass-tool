"""Общее состояние окна: база, выбранный период, сигналы между экранами."""

from datetime import date

from PySide6.QtCore import QObject, Signal

from ..db import Database


def shift_period(period: str, months: int) -> str:
    y, m = map(int, period.split("-"))
    m += months
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f"{y:04d}-{m:02d}"


class AppState(QObject):
    period_changed = Signal(str)
    data_changed = Signal()                # клиенты/документы изменились
    open_client = Signal(int)
    navigate = Signal(str)                 # имя экрана
    archives_dropped = Signal(list)        # новые архивы на приемку
    busy_changed = Signal(str)             # текст статуса фоновой работы ('' — свободно)

    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        periods = db.periods()
        self.period = periods[0] if periods else date.today().strftime("%Y-%m")

    def set_period(self, period: str):
        if period != self.period:
            self.period = period
            self.period_changed.emit(period)
