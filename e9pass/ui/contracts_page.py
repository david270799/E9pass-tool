"""Договоры → Excel: ФИО, дата рождения, телефон и e-mail из бланков E9PASS (서비스 신청서)."""

from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHeaderView, QMessageBox, QTableWidget,
                               QTableWidgetItem, QWidget)

from .. import contracts
from . import theme
from .widgets import BusyBar, DropZone, button, frame, hbox, label, run_task, vbox, wrap_label

# колонка таблицы → поле ContractData (None — только для показа)
COLUMNS = [("№", None, 44), ("ФИО", "full_name", 300), ("Дата рождения", "birth_date", 120),
           ("Телефон", "phone", 140), ("E-mail", "email", 230), ("Файл", None, 200), ("Замечания", None, 250)]
EDITABLE = {1, 2, 3, 4}


class ContractsPage(QWidget):
    def __init__(self, state):
        super().__init__()
        self.setObjectName("Page")
        self.state = state
        self.rows = []          # ContractData — в порядке добавления
        self.hashes = set()     # повторно тот же файл не читаем
        self.running = False

        title = label("Договоры → Excel", h1=True)
        self.stats = label("", muted=True)
        self.export_b = button("Скачать Excel", primary=True, on_click=self.export)
        self.export_b.setToolTip("ФИО, дата рождения, телефон, e-mail. Этот файл можно загрузить кнопкой "
                                 "«Импорт реестра» на главном экране — телефоны и e-mail попадут в карточки клиентов")
        self.clear_b = button("Очистить список", on_click=self.clear)
        intro = wrap_label(
            "Перетащите договоры E9PASS (서비스 신청서) — PDF, можно сразу много или папку. Программа возьмет "
            "ФИО, дату рождения, телефон и e-mail. Ошибку можно исправить прямо в таблице (двойной щелчок), "
            "потом — «Скачать Excel». Если договор — скан или фото, он читается OCR (дольше).", muted=True)
        drop = DropZone("Перетащите договоры", "PDF или фото бланка · можно папку целиком", contracts.CONTRACT_EXTENSIONS,
                        "Договоры (*.pdf *.jpg *.jpeg *.png)", compact=True)
        drop.files_dropped.connect(self.add_files)
        self.busy = BusyBar()

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([c for c, _, _ in COLUMNS])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.setTextElideMode(Qt.ElideRight)
        h = self.table.horizontalHeader()
        for i, (_, _, w) in enumerate(COLUMNS):
            self.table.setColumnWidth(i, w)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.itemChanged.connect(self._edited)
        self.empty = label("Здесь появятся данные из договоров.", muted=True)
        self.empty.setAlignment(Qt.AlignCenter)

        panel = frame("panel")
        panel.setLayout(vbox(self.table, self.empty, spacing=0, margins=(10, 10, 10, 10)))
        head = hbox(vbox(title, self.stats, spacing=2), self.clear_b, self.export_b, spacing=12)
        head.setStretch(0, 1)
        self.setLayout(vbox(head, intro, drop, self.busy, panel, spacing=14, margins=(32, 24, 32, 20)))
        self.refresh()

    # ------------------------------------------------------------ добавление
    def add_files(self, files):
        if self.running:
            QMessageBox.information(self, "Договоры", "Подождите — предыдущие файлы еще читаются.")
            return
        new, skipped = [], 0
        for f in files:
            f = Path(f)
            if f.suffix.lower() not in contracts.CONTRACT_EXTENSIONS:
                continue
            h = contracts.file_hash(f)
            if h in self.hashes:
                skipped += 1
                continue
            self.hashes.add(h)
            new.append(f)
        if skipped:
            self.state.busy_changed.emit(f"Договоры: повторы пропущены — {skipped}")
        if not new:
            if skipped:
                QMessageBox.information(self, "Договоры", f"Эти файлы уже в списке (повторов: {skipped}).")
            return
        self.running = True
        self.busy.start("Читаю договоры…", f"0 из {len(new)}")
        self._set_enabled(False)

        def done(rows):
            self.running = False
            self.busy.stop()
            self._set_enabled(True)
            self.state.busy_changed.emit("")
            self.rows += rows
            self.refresh()
            bad = [r for r in rows if r.problems]
            if bad:
                QMessageBox.information(self, "Договоры", f"Прочитано: {len(rows)}. С замечаниями: {len(bad)} — "
                                        "они подсвечены в таблице, данные можно вписать вручную.")

        def failed(err):
            self.running = False
            self.busy.stop()
            self._set_enabled(True)
            QMessageBox.warning(self, "Договоры", err)

        run_task(contracts.read_contracts, new, on_done=done, on_fail=failed,
                 on_progress=lambda m: (self.busy.set_text(m), self.state.busy_changed.emit(m)))

    def _set_enabled(self, on: bool):
        for w in (self.export_b, self.clear_b):
            w.setEnabled(on)

    def clear(self):
        if self.rows and QMessageBox.question(self, "Очистить", f"Убрать из списка все договоры ({len(self.rows)})?\n"
                                              "Сами файлы не удаляются.") != QMessageBox.Yes:
            return
        self.rows, self.hashes = [], set()
        self.refresh()

    # ------------------------------------------------------------ таблица
    def refresh(self):
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            values = [str(r + 1), row.full_name, row.birth_date, row.phone, row.email, row.file,
                      ", ".join(row.problems)]
            for c, text in enumerate(values):
                it = QTableWidgetItem(text)
                if c not in EDITABLE:
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                if c == 5:
                    it.setToolTip(f"{row.file}\nпрочитано: {row.source}" if row.source else row.file)
                if c == 6 and row.problems:
                    it.setForeground(Qt.yellow)
                    it.setToolTip(text)
                if c in EDITABLE and not text:
                    it.setToolTip("Пусто — двойной щелчок, чтобы вписать")
                self.table.setItem(r, c, it)
        self.table.blockSignals(False)
        self.table.setVisible(bool(self.rows))
        self.empty.setVisible(not self.rows)
        bad = sum(1 for x in self.rows if x.problems)
        self.stats.setText(f"договоров: {len(self.rows)} · без замечаний: {len(self.rows) - bad}"
                           + (f" · с замечаниями: {bad}" if bad else "") if self.rows else
                           "договоров пока нет")
        self.export_b.setEnabled(bool(self.rows) and not self.running)
        self.stats.setStyleSheet(f"color:{theme.ACCENT};" if bad else "")

    def _edited(self, item):
        field = COLUMNS[item.column()][1]
        if not field or item.row() >= len(self.rows):
            return
        text = " ".join(item.text().split())
        if field == "full_name":
            text = text.upper()
        elif field == "email":
            text = text.lower()
        elif field == "phone":
            text = contracts.normalize_mobile(text) or text
        elif field == "birth_date":
            text = contracts.normalize_birth(text) or text
        setattr(self.rows[item.row()], field, text)
        self.refresh()

    # ------------------------------------------------------------ экспорт
    def export(self):
        if not self.rows:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Куда сохранить Excel", f"Договоры_{date.today():%Y-%m-%d}.xlsx",
                                              "Excel (*.xlsx)")
        if not path:
            return
        try:
            contracts.export_contracts(self.rows, Path(path))
        except PermissionError:
            QMessageBox.warning(self, "Ошибка", "Файл открыт в Excel. Закройте его и повторите.")
            return
        QMessageBox.information(self, "Готово", f"Сохранено: {path}\nСтрок: {len(self.rows)}")
