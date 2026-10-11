"""Шаг 1: приемка архивов — очередь, распознавание, проверка типов фото."""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QGridLayout, QLabel, QListWidget, QListWidgetItem,
                               QMessageBox, QWidget)

from .. import services
from ..archive import ARCHIVE_EXTENSIONS
from ..db import BUILTIN_BACK, BUILTIN_BANK, BUILTIN_FRONT
from . import theme
from .widgets import (BusyBar, DropZone, NoWheelComboBox, badge, button, clear_layout, frame, hbox, label, open_path, run_task,
                      thumbnail, vbox, wrap_label)

PHOTO_TYPES = [(BUILTIN_FRONT, "ID лицо"), (BUILTIN_BACK, "ID оборот"), (BUILTIN_BANK, "통장사본"),
               ("", "Не определено / лишний файл")]


@dataclass
class QueueItem:
    archive: Path
    period: str
    status: str = "в очереди"         # в очереди / обработка / готово / ошибка
    detail: str = ""
    result: Optional[services.IntakeResult] = None


class IntakePage(QWidget):
    def __init__(self, state, on_status=None):
        super().__init__()
        self.setObjectName("Page")
        self.state = state
        self.on_status = on_status or (lambda text, done, total: None)
        self.queue: List[QueueItem] = []
        self.running = False

        self.title = label("Приемка документов", h1=True)
        self.steps = label("", muted=True)
        drop = DropZone("Перетащите еще архивы", ".zip / .rar · один архив = один клиент",
                        ARCHIVE_EXTENSIONS, "Архивы (*.zip *.rar)")
        drop.files_dropped.connect(self.state.archives_dropped.emit)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self.show_item)
        left = vbox(drop, label("ОЧЕРЕДЬ", caps=True), self.list, spacing=10)

        self.detail = frame("panel")
        self.detail_lay = vbox(spacing=0, margins=(0, 0, 0, 0))
        self.detail.setLayout(self.detail_lay)

        body = QGridLayout()
        body.setSpacing(16)
        lw = QWidget()
        lw.setLayout(left)
        lw.setFixedWidth(340)
        body.addWidget(lw, 0, 0)
        body.addWidget(self.detail, 0, 1)
        body.setColumnStretch(1, 1)

        self.setLayout(vbox(hbox(self.title, None, self.steps), body, spacing=16, margins=(32, 24, 32, 20)))
        state.archives_dropped.connect(self.enqueue)
        self._render_placeholder()

    # ------------------------------------------------------------ очередь

    def enqueue(self, archives: List[Path]):
        known = {(q.archive, q.period) for q in self.queue}
        for a in archives:
            if (Path(a), self.state.period) not in known:
                self.queue.append(QueueItem(Path(a), self.state.period))
        self._render_list()
        if self.list.currentRow() < 0 and self.queue:
            self.list.setCurrentRow(0)
        self._next()

    def _next(self):
        if self.running:
            return
        item = next((q for q in self.queue if q.status == "в очереди"), None)
        if not item:
            self.on_status("", 0, 0)
            self.state.busy_changed.emit("")
            return
        self.running = True
        item.status = "обработка"
        self._render_list()
        if self.list.currentRow() == self.queue.index(item):
            self.show_item(self.list.currentRow())
        done = sum(1 for q in self.queue if q.status not in ("в очереди", "обработка"))
        total = len(self.queue)

        def progress(msg):
            item.detail = msg
            bar = getattr(self, "live_bar", None)
            if bar is not None and self.list.currentRow() == self.queue.index(item):
                try:
                    bar.set_text(msg)
                except RuntimeError:  # виджет уже удален
                    self.live_bar = None
            text = f"{done + 1} / {total} · {item.archive.name} · {msg}"
            self.on_status(text, done, total)
            self.state.busy_changed.emit(f"Приемка: {text}")
            self._render_list()

        def finished(result: services.IntakeResult):
            item.result = result
            if result.pending:
                last = result.duplicates[0]
                item.status = "повтор"
                item.detail = f"уже добавлен {(last.created_at or '')[:10]} · период {last.batch_period}"
            else:
                item.status = "ошибка" if result.error else "готово"
                item.detail = result.error or ("; ".join(result.warnings) if result.warnings else "все данные найдены")
            self.running = False
            self._render_list()
            self.state.data_changed.emit()
            if self.queue.index(item) == self.list.currentRow():
                self.show_item(self.list.currentRow())
            self._next()

        def failed(err):
            item.status, item.detail = "ошибка", err
            self.running = False
            self._render_list()
            self._next()

        run_task(services.intake_archive, self.state.db, item.period, item.archive,
                 on_done=finished, on_fail=failed, on_progress=progress)

    def _render_list(self):
        row = self.list.currentRow()
        self.list.blockSignals(True)
        self.list.clear()
        for q in self.queue:
            mark = {"готово": "✓", "ошибка": "✕", "обработка": "…", "в очереди": "·", "повтор": "⛔",
                    "не принят": "—"}[q.status]
            if q.status == "готово" and q.result and q.result.warnings:
                mark = "⚠"
            it = QListWidgetItem(f"{mark}  {q.archive.name}\n     {q.status} · {q.detail}"[:120])
            if mark in ("⚠", "✕", "⛔"):
                it.setForeground(Qt.yellow)
            self.list.addItem(it)
        self.list.setCurrentRow(row if 0 <= row < len(self.queue) else -1)
        self.list.blockSignals(False)
        done = sum(1 for q in self.queue if q.status not in ("в очереди", "обработка"))
        self.title.setText(f"Приемка документов · {len(self.queue)} архив(ов)" if self.queue else
                           "Приемка документов")
        self.steps.setText(f"Обработано {done} из {len(self.queue)}" if self.queue else "")

    # ------------------------------------------------------------ детали

    def _render_placeholder(self):
        clear_layout(self.detail_lay)
        msg = wrap_label("Перетащите архивы клиентов (.zip / .rar). В каждом архиве — фото лицевой стороны ID, "
                         "оборота ID и банковской книжки (통장사본).\n\nДоговоры и другие документы "
                         "добавляются позже в карточке клиента.", muted=True)
        msg.setAlignment(Qt.AlignCenter)
        self.detail_lay.addStretch(1)
        self.detail_lay.addWidget(msg)
        self.detail_lay.addStretch(1)
        self.detail_lay.setContentsMargins(40, 40, 40, 40)

    def show_item(self, row: int):
        if not (0 <= row < len(self.queue)):
            self._render_placeholder()
            return
        q = self.queue[row]
        clear_layout(self.detail_lay)
        self.detail_lay.setContentsMargins(0, 0, 0, 0)
        if q.result is not None and q.result.pending:
            self._render_duplicate(q, row)
            return
        if not q.result or not q.result.client_id:
            self.detail_lay.setContentsMargins(40, 40, 40, 40)
            self.detail_lay.addStretch(1)
            if q.status == "обработка":
                bar = BusyBar()
                bar.start(f"Обрабатываю {q.archive.name}", q.detail or "загрузка модели распознавания…")
                self.live_bar = bar
                self.detail_lay.addWidget(bar)
                self.detail_lay.addWidget(wrap_label("Распаковка → определение фото → OCR лица, оборота и 통장 → "
                                                     "лист А4. Обычно до минуты на архив.", dim=True))
            else:
                text = q.detail if q.status == "ошибка" else f"{q.status}… {q.detail}"
                m = wrap_label(f"{q.archive.name}\n\n{text}", muted=q.status != "ошибка", warn=q.status == "ошибка")
                m.setAlignment(Qt.AlignCenter)
                self.detail_lay.addWidget(m)
                if q.status in ("ошибка", "не принят"):
                    self.detail_lay.addLayout(hbox(None, button("Повторить", on_click=lambda: self.retry(row)), None))
            self.detail_lay.addStretch(1)
            return
        db = self.state.db
        client = db.client(q.result.client_id)
        if not client:
            self._render_placeholder()
            return
        name = label(client.full_name or "(имя не распознано)")
        name.setStyleSheet("font-size:18px;font-weight:800;")
        src = label(f"{q.archive.name} →", dim=True)
        tag = badge("new", "добавлено к существующему" if q.result.merged_into_existing else "новый клиент")
        info = label(f"ARC {client.arc_number or '—'} · рожд. {client.birth_date or '—'} · "
                     f"{client.nationality or '—'}", muted=True)
        addr = label(f"Адрес: {client.registered_address or 'не распознан'}",
                     muted=bool(client.registered_address), warn=not client.registered_address)
        head = vbox(hbox(src, name, tag, None, badge(client.status)), info, addr, spacing=4)
        hw = QWidget()
        hw.setLayout(head)
        head.setContentsMargins(18, 16, 18, 12)
        self.detail_lay.addWidget(hw)

        grid = QGridLayout()
        grid.setContentsMargins(18, 6, 18, 6)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        for col, text in enumerate(("", "Файл", "Тип", "Как определен")):
            grid.addWidget(label(text, dim=True), 0, col)
        docs = [d for d in db.documents(client.id) if not d.is_processed_scan]
        for r, d in enumerate(docs, start=1):
            thumb = QLabel()
            pm = thumbnail(d.path, 64)
            if pm:
                thumb.setPixmap(pm)
            thumb.setFixedSize(66, 50)
            thumb.setAlignment(Qt.AlignCenter)
            thumb.setCursor(Qt.PointingHandCursor)
            thumb.mousePressEvent = lambda _e, p=d.path: open_path(p)
            combo = NoWheelComboBox()
            for code, text in PHOTO_TYPES:
                combo.addItem(text, code)
            idx = next((i for i, (c, _) in enumerate(PHOTO_TYPES) if c == d.doc_type), 3)
            combo.setCurrentIndex(idx)
            if not d.doc_type:
                combo.setProperty("warn", True)
            combo.currentIndexChanged.connect(
                lambda _i, cb=combo, doc_id=d.id: db.update_document(doc_id, doc_type=cb.currentData()))
            grid.addWidget(thumb, r, 0)
            grid.addWidget(label(d.path.name), r, 1)
            grid.addWidget(combo, r, 2)
            grid.addWidget(label(d.detected_by, muted=True), r, 3)
        grid.setColumnStretch(3, 1)
        gw = QWidget()
        gw.setLayout(grid)
        self.detail_lay.addWidget(gw)

        if client.warnings:
            box = frame("warnbox")
            box.setLayout(vbox(wrap_label("⚠ Проверьте: " + "; ".join(client.warnings), warn=True),
                               margins=(14, 10, 14, 10)))
            wrapper = QWidget()
            wrapper.setLayout(vbox(box, margins=(18, 6, 18, 6)))
            self.detail_lay.addWidget(wrapper)
        self.detail_lay.addStretch(1)

        reread = button("Перечитать данные", on_click=lambda: self.reread(client.id, row))
        reread.setToolTip("Если вы поменяли тип фото — заново распознать лицо, оборот и 통장")
        open_card = button("Открыть карточку", primary=True,
                           on_click=lambda: self.state.open_client.emit(client.id))
        hint = wrap_label("Фото не распозналось — выберите тип вручную. Данные правятся в карточке.", dim=True)
        hint.setMinimumWidth(10)
        footer = QFrame()
        footer.setObjectName("IntakeFooter")
        delete_b = button("Удалить клиента", on_click=lambda: self.delete_client(client.id, row))
        delete_b.setStyleSheet(f"QPushButton{{border-color:{theme.ACCENT};color:{theme.ACCENT};}}")
        delete_b.setToolTip("Удалить клиента со всеми файлами; архив можно загрузить заново")
        footer.setLayout(hbox(hint, None, delete_b, reread, open_card, margins=(18, 12, 18, 14)))
        footer.setStyleSheet("QFrame#IntakeFooter{border-top:1px solid rgba(255,255,255,0.10);}")
        self.detail_lay.addWidget(footer)

    def _render_duplicate(self, q, row):
        r = q.result
        f = r.fields
        self.detail_lay.setContentsMargins(24, 24, 24, 24)
        title = label("Повтор: этот клиент уже добавлялся меньше года назад", h2=True)
        title.setStyleSheet(f"color:{theme.ACCENT};font-size:18px;font-weight:800;")
        self.detail_lay.addWidget(title)
        self.detail_lay.addWidget(wrap_label(
            f"Из архива {q.archive.name} распознан: {f.get('full_name') or 'без имени'} · ARC {f.get('arc_number')}",
            muted=True))
        box = frame("warnbox")
        lines = "\n".join(f"• {services.describe_client(c)}" for c in r.duplicates)
        box.setLayout(vbox(wrap_label(f"Уже есть в программе:\n{lines}", warn=True), margins=(14, 10, 14, 10)))
        self.detail_lay.addWidget(box)
        self.detail_lay.addWidget(wrap_label(
            "Повторно принять клиента можно, только если с прошлого добавления прошел год. "
            "Если прежняя запись ошибочная — удалите ее и примите архив заново (распознанные данные сохранены, "
            "повторного OCR не будет).", dim=True))
        self.detail_lay.addStretch(1)
        open_old = button("Открыть прежнего", on_click=lambda: self.state.open_client.emit(r.duplicates[0].id))
        replace = button("Удалить прежнего и принять", primary=True, on_click=lambda: self._accept_duplicate(row))
        reject = button("Не принимать", on_click=lambda: self._reject_duplicate(row))
        self.detail_lay.addLayout(hbox(None, open_old, reject, replace))

    def _accept_duplicate(self, row):
        q = self.queue[row]
        old = "\n".join(f"• {services.describe_client(c)}" for c in q.result.duplicates)
        if QMessageBox.question(self, "Удалить прежнего", f"Удалить прежнюю запись со всеми файлами?\n\n{old}",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.state.busy_changed.emit("Принимаю архив…")

        def done(result):
            q.result, q.status = result, "готово"
            q.detail = "; ".join(result.warnings) if result.warnings else "принят заново"
            self.state.busy_changed.emit("")
            self._render_list()
            self.show_item(row)
            self.state.data_changed.emit()

        run_task(services.replace_duplicates_and_finalize, self.state.db, q.result, on_done=done,
                 on_fail=lambda err: (self.state.busy_changed.emit(""), QMessageBox.warning(self, "Ошибка", err)))

    def _reject_duplicate(self, row):
        q = self.queue[row]
        services.discard_intake(q.result)
        q.result, q.status, q.detail = None, "не принят", "повтор — архив не принят"
        self._render_list()
        self.show_item(row)

    def delete_client(self, client_id: int, row: int):
        from .client import confirm_delete_client
        c = self.state.db.client(client_id)
        if not c or not confirm_delete_client(self, c):
            return
        services.delete_client_everything(self.state.db, client_id)
        q = self.queue[row]
        q.status, q.detail, q.result = "ошибка", "клиент удален — можно загрузить заново («Повторить»)", None
        self._render_list()
        self.show_item(row)
        self.state.data_changed.emit()

    def retry(self, row: int):
        q = self.queue[row]
        q.status, q.detail, q.result = "в очереди", "", None
        self._render_list()
        self.show_item(row)
        self._next()

    def reread(self, client_id: int, row: int):
        self.state.busy_changed.emit("Перечитываю данные…")
        run_task(services.reread_client, self.state.db, client_id,
                 on_done=lambda _r: (self.state.busy_changed.emit(""), self.state.data_changed.emit(),
                                     self.show_item(row)),
                 on_fail=lambda err: (self.state.busy_changed.emit(""), QMessageBox.warning(self, "Ошибка", err)))
