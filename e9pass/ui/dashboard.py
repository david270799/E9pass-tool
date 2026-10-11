"""Шаг 1: дашборд периода — архивы, счетчики, карточки клиентов, экспорт реестра."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QFileDialog, QFrame, QGridLayout, QLineEdit, QMessageBox,
                               QProgressBar, QScrollArea, QWidget)

from .. import services
from ..archive import ARCHIVE_EXTENSIONS
from ..db import BUILTIN_A4, BUILTIN_BACK, BUILTIN_BANK, BUILTIN_FRONT
from . import theme
from .widgets import (MONTHS_RU, DropZone, FlowLayout, badge, button, chip, clear_layout, frame, hbox, label,
                      run_task, stat_tile, vbox)


class ClientCard(QFrame):
    def __init__(self, state, client, docs, rule_names):
        super().__init__()
        self.state, self.client_id = state, client.id
        self.setProperty("card", True)
        self.setCursor(Qt.PointingHandCursor)
        name = label(client.full_name or "(без имени)")
        name.setStyleSheet("font-weight:700;font-size:15px;")
        rm = button("✕", flat=True, on_click=self.delete)
        rm.setFixedSize(28, 28)
        rm.setToolTip("Удалить клиента со всеми данными")
        rm.setStyleSheet(f"QPushButton{{color:{theme.DIM};font-size:14px;min-height:0;padding:0;}}"
                         f"QPushButton:hover{{color:{theme.ACCENT};}}")
        top = hbox(name, None, badge(client.status), rm, spacing=8)
        arc = label(client.arc_number or "—", mono=True)
        info = label(" · ".join(x for x in (client.nationality, client.birth_date) if x), muted=True)
        addr = label(client.registered_address or "Адрес не распознан")
        addr.setStyleSheet(f"font-size:12.5px;color:{theme.MUTED if client.registered_address else theme.ACCENT};")
        addr.setMinimumWidth(10)
        self.setMinimumWidth(10)
        name.setMinimumWidth(10)
        types = {d.doc_type for d in docs}
        chips = FlowLayout(6)
        for code, text in ((BUILTIN_FRONT, "ID лицо"), (BUILTIN_BACK, "ID оборот"), (BUILTIN_BANK, "통장")):
            chips.addWidget(chip(text, "ok" if code in types else "missing"))
        extra = [rule_names.get(t, t) for t in types
                 if t and t not in (BUILTIN_FRONT, BUILTIN_BACK, BUILTIN_BANK, BUILTIN_A4)]
        for t in sorted(extra)[:3]:
            chips.addWidget(chip(t))
        if any(d.password_status == "locked" for d in docs):
            chips.addWidget(chip("PDF с паролем", "warn"))
        if client.master_pdf:
            chips.addWidget(chip("Master-PDF"))
        lay = vbox(top, hbox(arc, info, None, spacing=14), addr, chips, spacing=8, margins=(16, 14, 16, 14))
        if client.warnings:
            w = label("⚠ " + "; ".join(client.warnings[:2]), warn=True)
            w.setStyleSheet("font-size:12px;")
            lay.addWidget(w)
        self.setLayout(lay)

    def mouseReleaseEvent(self, e):
        self.state.open_client.emit(self.client_id)

    def delete(self):
        from .client import confirm_delete_client
        c = self.state.db.client(self.client_id)
        if c and confirm_delete_client(self, c):
            services.delete_client_everything(self.state.db, c.id)
            self.state.data_changed.emit()


class DashboardPage(QWidget):
    def __init__(self, state):
        super().__init__()
        self.setObjectName("Page")
        self.state = state
        self.filter = "all"

        self.month_label = label("", h1=True)
        self.period_label = label("", mono=True)
        self.period_label.setStyleSheet("font-weight:800;font-size:16px;padding:0 6px;")
        prev_b = button("‹", flat=True, on_click=lambda: self._shift(-1))
        next_b = button("›", flat=True, on_click=lambda: self._shift(1))
        prev_b.setToolTip("Предыдущий месяц")
        next_b.setToolTip("Следующий месяц")
        switcher = QFrame()
        switcher.setObjectName("Switcher")
        switcher.setStyleSheet(f"QFrame#Switcher{{border:1px solid {theme.TEXT};border-radius:12px;}}")
        switcher.setLayout(hbox(prev_b, self.period_label, next_b, spacing=0, margins=(4, 2, 4, 2)))

        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по ФИО или ARC")
        self.search.setFixedWidth(240)
        self.search.textChanged.connect(self.refresh)
        export_b = button("Экспорт реестра (ID + Адрес)", primary=True, on_click=self.export_registry)

        drop = DropZone("Перетащите архивы .zip / .rar", "в любое место окна или нажмите здесь · один архив = один клиент",
                        ARCHIVE_EXTENSIONS, "Архивы (*.zip *.rar)")
        drop.files_dropped.connect(self.state.archives_dropped.emit)
        self.t_clients = stat_tile("0", "клиентов в периоде")
        self.t_review = stat_tile("0", "нужно проверить", accent=True)
        self.t_master = stat_tile("0", "Master-PDF собрано")
        self.t_printed = stat_tile("0", "напечатано")
        tiles = QGridLayout()
        tiles.setSpacing(14)
        tiles.addWidget(drop, 0, 0)
        for i, t in enumerate((self.t_clients, self.t_review, self.t_master, self.t_printed), start=1):
            tiles.addWidget(t, 0, i)
        tiles.setColumnStretch(0, 3)
        for i in range(1, 5):
            tiles.setColumnStretch(i, 2)

        # полоса прогресса приемки
        self.import_bar = frame("panel")
        self.import_text = label("", muted=True)
        self.import_progress = QProgressBar()
        self.import_progress.setTextVisible(False)
        open_intake = button("Открыть приемку →", flat=True, on_click=lambda: self.state.navigate.emit("intake"))
        open_intake.setStyleSheet(f"color:{theme.ACCENT};font-weight:700;")
        t = label("Импорт")
        t.setStyleSheet(f"color:{theme.ACCENT};font-weight:700;")
        self.import_bar.setLayout(hbox(t, self.import_text, self.import_progress, open_intake, spacing=14,
                                       margins=(16, 8, 16, 8)))
        self.import_bar.hide()

        self.filter_group = QButtonGroup(self)
        self.filter_buttons = {}
        filters = hbox(spacing=8)
        for key in ("all", "review", "ready"):
            b = button("", chip=True)
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=key: self._set_filter(k))
            self.filter_group.addButton(b)
            self.filter_buttons[key] = b
            filters.addWidget(b)
        self.filter_buttons["all"].setChecked(True)
        filters.addStretch(1)

        self.grid = QGridLayout()
        self.grid.setSpacing(14)
        self.grid.setAlignment(Qt.AlignTop)
        grid_host = QWidget()
        grid_host.setLayout(self.grid)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(grid_host)
        self.empty = label("В этом месяце пока нет клиентов. Перетащите архивы в зону выше.", muted=True)
        self.empty.setAlignment(Qt.AlignCenter)

        zip_all = button("Экспорт в ZIP (все клиенты)", on_click=self.export_zip_all)
        footer = hbox(label("Реестр: №, ФИО (заглавными и строчными), Номер ARC, Дата рождения, "
                            "Гражданство, Адрес регистрации — без банковских данных", dim=True), None, zip_all)

        root = vbox(hbox(switcher, self.month_label, None, self.search, export_b, spacing=14),
                    tiles, self.import_bar, filters, self.empty, scroll, footer,
                    spacing=16, margins=(32, 24, 32, 20))
        self.setLayout(root)
        state.period_changed.connect(lambda _: self.refresh())
        state.data_changed.connect(self.refresh)
        self.refresh()

    def _shift(self, months):
        from .state import shift_period
        self.state.set_period(shift_period(self.state.period, months))

    def _set_filter(self, key):
        self.filter = key
        self.refresh()

    def set_import_status(self, text: str, done: int, total: int):
        self.import_bar.setVisible(bool(text))
        self.import_text.setText(text)
        self.import_progress.setMaximum(max(total, 1))
        self.import_progress.setValue(done)

    def refresh(self):
        p = self.state.period
        y, m = p.split("-")
        self.month_label.setText(f"{MONTHS_RU[int(m) - 1]} {y}")
        self.period_label.setText(p)
        db = self.state.db
        clients = db.clients(p)
        review = [c for c in clients if c.status == "review"]
        ready = [c for c in clients if c.status == "ready"]
        self.t_clients.value_label.setText(str(len(clients)))
        self.t_review.value_label.setText(str(len(review)))
        self.t_master.value_label.setText(str(sum(1 for c in clients if c.master_pdf)))
        self.t_printed.value_label.setText(str(sum(1 for c in clients if c.printed)))
        self.filter_buttons["all"].setText(f"Все · {len(clients)}")
        self.filter_buttons["review"].setText(f"Проверить · {len(review)}")
        self.filter_buttons["ready"].setText(f"Готовы · {len(ready)}")
        shown = {"all": clients, "review": review, "ready": ready}[self.filter]
        q = self.search.text().strip().lower()
        if q:
            shown = [c for c in shown if q in c.full_name.lower() or q in c.arc_number]
        clear_layout(self.grid)
        names = db.rule_names()
        for i, c in enumerate(shown):
            self.grid.addWidget(ClientCard(self.state, c, db.documents(c.id), names), i // 3, i % 3)
        for col in range(3):
            self.grid.setColumnStretch(col, 1)
        self.empty.setVisible(not clients)

    # ------------------------------------------------------------ экспорт

    def export_registry(self):
        clients = self.state.db.clients(self.state.period)
        if not clients:
            QMessageBox.information(self, "Нет данных", "В этом месяце нет клиентов.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Куда сохранить реестр", f"REESTR_{self.state.period}.xlsx",
                                              "Excel (*.xlsx)")
        if not path:
            return
        try:
            services.export_registry(clients, Path(path))
        except PermissionError:
            QMessageBox.warning(self, "Ошибка", "Файл открыт в Excel. Закройте его и повторите.")
            return
        QMessageBox.information(self, "Готово", f"Реестр сохранен:\n{path}")

    def export_zip_all(self):
        clients = self.state.db.clients(self.state.period)
        if not clients:
            return
        folder = QFileDialog.getExistingDirectory(self, "Куда сохранить ZIP-архивы")
        if not folder:
            return
        db = self.state.db

        def job(progress):
            out = []
            for i, c in enumerate(clients, start=1):
                progress(f"ZIP {i}/{len(clients)}: {c.full_name}")
                out.append(services.export_client_zip(db, c.id, Path(folder)))
            return out

        self.state.busy_changed.emit("Экспорт ZIP…")
        run_task(job, on_progress=self.state.busy_changed.emit,
                 on_done=lambda res: (self.state.busy_changed.emit(""), self.state.data_changed.emit(),
                                      QMessageBox.information(self, "Готово",
                                                              f"Создано архивов: {len(res)}\n{folder}")),
                 on_fail=lambda err: (self.state.busy_changed.emit(""),
                                      QMessageBox.warning(self, "Ошибка", err)))
