"""Настройки: список типов документов и порядок Master-PDF (перетаскиванием)."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFileDialog, QGridLayout, QHeaderView, QLineEdit,
                               QMessageBox, QTreeWidget, QTreeWidgetItem, QWidget)

from .. import services
from ..db import BUILTIN_A4, DocRule
from . import theme
from .widgets import button, frame, hbox, label, vbox, wrap_label

ROLE_ID = Qt.UserRole


class RulesTree(QTreeWidget):
    """Список с перетаскиванием строк; после переноса вызывает on_reordered."""

    def __init__(self, on_reordered):
        super().__init__()
        self.on_reordered = on_reordered
        self.setColumnCount(6)
        self.setHeaderLabels(["№", "Тип документа", "Маски имени файла", "Слова OCR", "Master-PDF", "Печать"])
        self.setRootIsDecorated(False)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setUniformRowHeights(True)
        h = self.header()
        h.setSectionResizeMode(QHeaderView.Interactive)
        h.setStretchLastSection(False)
        for col, w in enumerate((44, 200, 230, 230, 96, 80)):
            self.setColumnWidth(col, w)
        h.setSectionResizeMode(3, QHeaderView.Stretch)

    def dropEvent(self, e):
        super().dropEvent(e)
        self.on_reordered()


class DocTypesPage(QWidget):
    def __init__(self, state):
        super().__init__()
        self.setObjectName("Page")
        self.state = state
        self.current = None

        add_b = button("+  Добавить тип", primary=True, on_click=self.add_rule)
        restore_b = button("Восстановить стандартные", on_click=self.restore_defaults)
        restore_b.setToolTip("Вернуть удаленные стандартные типы (Договор, Страховка…) и их маски, "
                             "например {ФИО} → Договор. Ваши типы и маски не меняются.")
        self.tree = RulesTree(self.save_order)
        self.tree.currentItemChanged.connect(lambda cur, _prev: self.edit(cur))
        hint = wrap_label("Перетаскивайте строки, чтобы менять порядок Master-PDF. Если у клиента документа "
                          "этого типа нет, он просто пропускается. Встроенные типы (фото ID и 통장) нельзя удалить.",
                          dim=True)

        # редактор
        self.f_name = QLineEdit()
        self.f_patterns = QLineEdit()
        self.f_patterns.setPlaceholderText("*계약*, *contract*")
        self.f_keywords = QLineEdit()
        self.f_keywords.setPlaceholderText("근로계약서, 표준근로계약서")
        self.f_master = QCheckBox("Включать в Master-PDF")
        self.f_print = QCheckBox("Печатать в Шаге 3 (вместе с ID)")
        self.edit_title = label("РЕДАКТИРОВАНИЕ", caps=True)
        self.builtin_note = label("", dim=True)
        self.test_result = wrap_label("", muted=True)
        self.btn_delete = button("Удалить тип", on_click=self.delete_rule)
        editor = frame("panel")
        editor.setStyleSheet(f"QFrame[panel='true']{{border:1px solid {theme.TEXT};}}")
        editor.setFixedWidth(400)
        editor.setLayout(vbox(
            self.edit_title, self.builtin_note,
            vbox(label("Название типа", dim=True), self.f_name, spacing=4),
            vbox(label("Маски имени файла через запятую (* — любые символы)", dim=True), self.f_patterns,
                 label("{ФИО} — файл назван именем клиента · re:… — шаблон, например re:0[1-9] для 01…09", dim=True),
                 spacing=4),
            vbox(label("Ключевые слова OCR через запятую (если имя не подошло)", dim=True), self.f_keywords, spacing=4),
            self.f_master, self.f_print,
            button("Проверить правило на файле…", on_click=self.test_file), self.test_result,
            None,
            hbox(self.btn_delete, button("Сохранить", primary=True, on_click=self.save_rule)),
            spacing=12, margins=(18, 16, 18, 16)))

        body = QGridLayout()
        body.setSpacing(16)
        body.addLayout(vbox(self.tree, hint, spacing=8), 0, 0)
        body.addWidget(editor, 0, 1)
        body.setColumnStretch(0, 1)
        self.setLayout(vbox(hbox(vbox(label("Настройки", muted=True), label("Типы документов и очередь Master-PDF",
                                                                           h1=True), spacing=2), None, restore_b, add_b),
                            body, spacing=16, margins=(32, 24, 32, 20)))
        self.refresh()

    def refresh(self, select_id=None):
        self.tree.blockSignals(True)
        self.tree.clear()
        n = 0
        for r in self.state.db.rules():
            in_queue = r.in_master
            n += 1 if in_queue else 0
            it = QTreeWidgetItem([
                str(n) if in_queue else "—",
                r.doc_name + ("  · встроенный" if r.builtin else ""),
                ", ".join(r.filename_patterns) or "—",
                ", ".join(r.ocr_keywords) or "—",
                "✓" if r.in_master else "",
                "✓" if r.print_step3 else "",
            ])
            it.setData(0, ROLE_ID, r.id)
            it.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsDragEnabled)
            for col in (4, 5):
                it.setTextAlignment(col, Qt.AlignCenter)
                it.setForeground(col, Qt.yellow)
            if not in_queue:
                for col in range(4):
                    it.setForeground(col, Qt.gray)
            self.tree.addTopLevelItem(it)
            if select_id == r.id:
                self.tree.setCurrentItem(it)
        self.tree.blockSignals(False)
        if self.tree.currentItem() is None and self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(1 if self.tree.topLevelItemCount() > 1 else 0))
        self.edit(self.tree.currentItem())

    def _rule(self, rule_id):
        return next((r for r in self.state.db.rules() if r.id == rule_id), None)

    def edit(self, item):
        r = self._rule(item.data(0, ROLE_ID)) if item else None
        self.current = r
        if not r:
            return
        self.edit_title.setText(f"РЕДАКТИРОВАНИЕ · {r.doc_name.upper()}")
        self.f_name.setText(r.doc_name)
        self.f_patterns.setText(", ".join(r.filename_patterns))
        self.f_keywords.setText(", ".join(r.ocr_keywords))
        self.f_master.setChecked(bool(r.in_master))
        self.f_print.setChecked(bool(r.print_step3))
        is_a4 = r.code == BUILTIN_A4
        self.f_patterns.setEnabled(not is_a4)
        self.f_keywords.setEnabled(not is_a4)
        self.btn_delete.setEnabled(not r.builtin)
        self.builtin_note.setText("Встроенный: создается программой из лица ID" if is_a4 else
                                  ("Встроенный тип (фото из архива клиента)" if r.builtin else ""))
        self.test_result.setText("")

    @staticmethod
    def _split(text):
        return [t.strip() for t in text.replace(";", ",").split(",") if t.strip()]

    def save_rule(self):
        r = self.current
        if not r:
            return
        name = self.f_name.text().strip()
        if not name:
            QMessageBox.warning(self, "Название", "Введите название типа.")
            return
        r.doc_name = name
        r.filename_patterns = self._split(self.f_patterns.text())
        r.ocr_keywords = self._split(self.f_keywords.text())
        r.in_master = int(self.f_master.isChecked())
        r.print_step3 = int(self.f_print.isChecked())
        self.state.db.save_rule(r)
        self.refresh(select_id=r.id)
        found = services.recheck_all_unknown(self.state.db)
        self.state.data_changed.emit()
        self.test_result.setText(f"✓ Сохранено. Нераспознанных документов определено заново: {found}")

    def add_rule(self):
        rules = self.state.db.rules()
        order = max((x.sort_order for x in rules if x.in_master), default=0) + 1
        new = DocRule(id=0, code="", doc_name="Новый документ", filename_patterns=[], ocr_keywords=[],
                      sort_order=order, in_master=1, print_step3=0, builtin=0)
        rid = self.state.db.save_rule(new)
        # сдвигаем встроенные фото-типы вниз, чтобы новый тип встал в конец очереди Master-PDF
        ids = [x.id for x in self.state.db.rules()]
        self.state.db.reorder_rules(ids)
        self.refresh(select_id=rid)
        self.f_name.setFocus()
        self.f_name.selectAll()

    def delete_rule(self):
        r = self.current
        if not r or r.builtin:
            return
        if QMessageBox.question(self, "Удалить тип", f"Удалить тип «{r.doc_name}»?\nДокументы этого типа станут "
                                "«не определен».") == QMessageBox.Yes:
            self.state.db.delete_rule(r.id)
            self.refresh()
            self.state.data_changed.emit()

    def restore_defaults(self):
        restored = self.state.db.restore_default_rules()
        if not restored:
            QMessageBox.information(self, "Стандартные типы", "Все стандартные типы и маски на месте.")
            return
        cur = self.current.id if self.current else None
        self.refresh(select_id=cur)
        found = services.recheck_all_unknown(self.state.db)
        self.state.data_changed.emit()
        QMessageBox.information(self, "Стандартные типы", "Восстановлено:\n• " + "\n• ".join(restored) +
                                f"\n\nНераспознанных документов определено заново: {found}")

    def save_order(self):
        ids = [self.tree.topLevelItem(i).data(0, ROLE_ID) for i in range(self.tree.topLevelItemCount())]
        self.state.db.reorder_rules(ids)
        cur = self.current.id if self.current else None
        self.refresh(select_id=cur)
        self.state.data_changed.emit()

    def test_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Файл для проверки", "", "Документы (*.pdf *.jpg *.jpeg *.png)")
        if not path:
            return
        from pathlib import Path
        rule = DocRule(0, self.current.code if self.current else "test", self.f_name.text(),
                       self._split(self.f_patterns.text()), self._split(self.f_keywords.text()), 0, 1, 0, 0)
        code, reason = services.classify_file(Path(path), [rule], None)
        self.test_result.setText(f"✓ Подходит: {reason}" if code else
                                 "✕ Не подходит по маске и тексту PDF (сканы проверяются OCR при добавлении)")
