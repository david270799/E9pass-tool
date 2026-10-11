"""Шаг 3: печать отчетности — [Договор клиента] ➔ [ID на А4] по очереди для отмеченных клиентов."""

from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPainter
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFileDialog, QGridLayout, QHeaderView, QListView,
                               QListWidget, QListWidgetItem, QMessageBox, QTableWidget, QTableWidgetItem, QWidget)

from .. import pdf_tools, services
from .widgets import button, frame, hbox, label, thumbnail, vbox, wrap_label


def print_pdf(pdf_path: Path, printer: QPrinter):
    """Печать PDF через системный принтер (рендер страниц PyMuPDF)."""
    import pymupdf

    painter = QPainter()
    if not painter.begin(printer):
        raise RuntimeError("Не удалось начать печать")
    dpi = min(printer.resolution(), 300)
    try:
        with pymupdf.open(pdf_path) as doc:
            for i, page in enumerate(doc):
                if i:
                    printer.newPage()
                zoom = dpi / 72
                pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
                rect = painter.viewport()
                size = img.size().scaled(rect.size(), Qt.KeepAspectRatio)
                target = QRect(rect.x() + (rect.width() - size.width()) // 2,
                               rect.y() + (rect.height() - size.height()) // 2, size.width(), size.height())
                painter.drawImage(target, img)
    finally:
        painter.end()


class PrintPage(QWidget):
    def __init__(self, state):
        super().__init__()
        self.setObjectName("Page")
        self.state = state
        self.checked = set()

        self.crumb = label("", muted=True)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["", "ФИО", "Для печати", "ID на А4"])
        self.table.verticalHeader().hide()
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        for col, w in ((0, 40), (2, 140), (3, 100)):
            self.table.setColumnWidth(col, w)
        self.table.itemChanged.connect(self._item_changed)
        self.count_label = label("", caps=True)
        select_all = button("Выбрать всех", small=True, on_click=self.select_all)
        clear_b = button("Снять все", small=True, on_click=self.clear_all)
        self.warn_box = frame("warnbox")
        self.warn_text = wrap_label("", warn=True)
        self.warn_box.setLayout(vbox(self.warn_text, margins=(14, 10, 14, 10)))
        left = frame("panel")
        left.setFixedWidth(560)
        left.setLayout(vbox(hbox(self.count_label, None, clear_b, select_all), self.table, self.warn_box,
                            spacing=10, margins=(14, 14, 14, 14)))

        self.seq = QListWidget()
        self.seq.setViewMode(QListView.IconMode)
        self.seq.setIconSize(QSize(96, 136))
        self.seq.setGridSize(QSize(140, 205))
        self.seq.setMovement(QListView.Static)
        self.seq.setResizeMode(QListView.Adjust)
        self.seq.setWordWrap(True)
        self.seq.setStyleSheet("QListWidget{border:none;background:transparent;} QListWidget::item{border:none;}")
        self.seq.itemDoubleClicked.connect(self._preview_item)
        self.duplex = QCheckBox("Двусторонняя печать")
        self.mark = QCheckBox("Отметить клиентов как «Напечатано»")
        self.mark.setChecked(True)
        self.total = label("", h2=True)
        self.total_sub = label("", muted=True)
        self.btn_save = button("", on_click=self.save_pdf)
        self.btn_print = button("Печать отчетности", primary=True, on_click=self.print_now)
        right = frame("panel")
        right.setLayout(vbox(hbox(label("ПОРЯДОК ПЕЧАТИ", caps=True), None,
                                  label("Договор ➔ ID ➔ Договор ➔ ID …", muted=True)),
                             self.seq, hbox(self.duplex, self.mark, None, spacing=18),
                             hbox(vbox(self.total, self.total_sub, spacing=2), None, self.btn_save, self.btn_print),
                             spacing=12, margins=(18, 16, 18, 16)))

        body = QGridLayout()
        body.setSpacing(18)
        body.addWidget(left, 0, 0)
        body.addWidget(right, 0, 1)
        body.setColumnStretch(1, 1)
        self.setLayout(vbox(vbox(self.crumb, label("Печать отчетности", h1=True), spacing=2), body,
                            spacing=16, margins=(32, 24, 32, 20)))
        state.period_changed.connect(lambda _: (self.checked.clear(), self.refresh()))
        state.data_changed.connect(lambda: self.isVisible() and self.refresh())

    # ------------------------------------------------------------ данные

    def refresh(self):
        db = self.state.db
        p = self.state.period
        self.crumb.setText(p)
        self.btn_save.setText(f"Сохранить BATCH_PRINT_{p}.pdf")
        clients = db.clients(p)
        plan = {c.id: (files, missing) for c, files, missing in services.print_plan(db, [c.id for c in clients])}
        self.table.blockSignals(True)
        self.table.setRowCount(len(clients))
        for row, c in enumerate(clients):
            files, missing = plan[c.id]
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Checked if c.id in self.checked else Qt.Unchecked)
            chk.setData(Qt.UserRole, c.id)
            self.table.setItem(row, 0, chk)
            name = QTableWidgetItem(c.full_name + ("  ✓ напечатан" if c.printed else ""))
            name.setToolTip(c.full_name)
            self.table.setItem(row, 1, name)
            id_missing = any(m.startswith("ID") for m in missing)
            doc_missing = [m for m in missing if not m.startswith("ID")]
            docs_files = [f for f in files if "_A4" not in f.name]
            pages = sum(pdf_tools.page_count(f) for f in docs_files)
            doc_item = QTableWidgetItem("нет: " + ", ".join(doc_missing) if doc_missing else f"{pages} стр.")
            id_item = QTableWidgetItem("нет скана" if id_missing else "готов")
            for it, bad in ((doc_item, doc_missing), (id_item, id_missing)):
                if bad:
                    it.setForeground(Qt.yellow)
            self.table.setItem(row, 2, doc_item)
            self.table.setItem(row, 3, id_item)
        self.table.blockSignals(False)
        self._update_plan()

    def _item_changed(self, item):
        if item.column() != 0:
            return
        cid = item.data(Qt.UserRole)
        if item.checkState() == Qt.Checked:
            self.checked.add(cid)
        else:
            self.checked.discard(cid)
        self._update_plan()

    def select_all(self):
        self.checked = {c.id for c in self.state.db.clients(self.state.period)}
        self.refresh()

    def clear_all(self):
        self.checked.clear()
        self.refresh()

    def _selected_ids(self):
        return [c.id for c in self.state.db.clients(self.state.period) if c.id in self.checked]

    def _update_plan(self):
        ids = self._selected_ids()
        total_clients = self.table.rowCount()
        plan = services.print_plan(self.state.db, ids)
        missing = [(c, m) for c, _f, m in plan if m]
        self.count_label.setText(f"КЛИЕНТЫ · ВЫБРАНО {len(ids)} ИЗ {total_clients}")
        if missing:
            text = "Не хватает документов — снимите галочку или догрузите их:\n" + "\n".join(
                f"• {c.full_name}: {', '.join(m)}" for c, m in missing)
            self.warn_text.setText(text)
        self.warn_box.setVisible(bool(missing))
        self.seq.clear()
        pages = 0
        n = 0
        for c, files, _m in plan:
            for f in files:
                n += 1
                count = pdf_tools.page_count(f)
                pages += count
                kind = "ID · А4" if "_A4" in f.name else f"Договор · {count} стр."
                it = QListWidgetItem(f"{n}. {kind}\n{c.full_name.split(' ')[0]}")
                it.setData(Qt.UserRole, str(f))
                it.setToolTip("Двойной щелчок — посмотреть")
                pm = thumbnail(f, 136)
                if pm:
                    it.setIcon(QIcon(pm))
                self.seq.addItem(it)
        self.total.setText(f"{pages} листов")
        self.total_sub.setText(f"{len(ids)} клиент(ов)")
        ok = bool(ids) and not missing and pages > 0
        self.btn_print.setEnabled(ok)
        self.btn_save.setEnabled(ok)

    def _preview_item(self, item):
        from .preview import show_preview
        path = item.data(Qt.UserRole)
        if path:
            show_preview(path, item.text().replace("\n", " · "), self)

    # ------------------------------------------------------------ вывод

    def _check(self) -> bool:
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, "Печать", "Отметьте клиентов галочками.")
            return False
        missing = [(c, m) for c, _f, m in services.print_plan(self.state.db, ids) if m]
        if missing:
            QMessageBox.warning(self, "Не хватает документов", "\n".join(
                f"{c.full_name}: {', '.join(m)}" for c, m in missing) + "\n\nСнимите галочку с этих клиентов.")
            return False
        return True

    def _build(self, out: Path) -> Path:
        return services.build_batch_print(self.state.db, self._selected_ids(), out)

    def save_pdf(self):
        if not self._check():
            return
        path, _ = QFileDialog.getSaveFileName(self, "Куда сохранить файл печати",
                                              f"BATCH_PRINT_{self.state.period}.pdf", "PDF (*.pdf)")
        if not path:
            return
        self._build(Path(path))
        self._after_print()
        QMessageBox.information(self, "Готово", f"Файл сохранен:\n{path}")

    def print_now(self):
        if not self._check():
            return
        from .. import config
        tmp = config.period_dir(self.state.period) / f"BATCH_PRINT_{self.state.period}.pdf"
        self._build(tmp)
        printer = QPrinter(QPrinter.HighResolution)
        printer.setDuplex(QPrinter.DuplexLongSide if self.duplex.isChecked() else QPrinter.DuplexNone)
        dlg = QPrintDialog(printer, self)
        dlg.setWindowTitle("Печать отчетности")
        if dlg.exec() != QPrintDialog.Accepted:
            return
        try:
            print_pdf(tmp, printer)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Ошибка печати", str(e))
            return
        self._after_print()

    def _after_print(self):
        if self.mark.isChecked():
            services.mark_printed(self.state.db, self._selected_ids())
            self.state.data_changed.emit()
