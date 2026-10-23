"""Страница «Обучение OCR»: разметка фото карт для словаря имен и дообучения моделей."""

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QGridLayout, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPushButton, QWidget)

from .. import training
from ..ocr.ocr_engine import load_image
from . import theme
from .region_view import RegionView
from .widgets import DropZone, button, clear_layout, frame, hbox, label, pil_to_pixmap, run_task, vbox, wrap_label

STATUS_TEXT = {training.NEW: ("ждет OCR", "#8C8C8C"), training.READY: ("проверить", "#F5C518"),
               training.DONE: ("готово", "#3DDC84"), training.SKIP: ("пропущено", "#8C8C8C")}
SIDE_TEXT = {training.FRONT: "лицо", training.BACK: "оборот", training.OTHER: "не карта", "": "?"}
FILTERS = [("Все", None), ("Проверить", training.READY), ("Готово", training.DONE), ("Пропуск", training.SKIP)]


class TrainingPage(QWidget):
    def __init__(self, state):
        super().__init__()
        self.setObjectName("Page")
        self.state = state
        self.store = training.TrainingStore()
        self.current = None
        self.editing = None  # карта, поля которой сейчас на экране (для автосохранения исправлений)
        self.filter = None
        self.ocr_running = False
        self.show_boxes = True  # галочка «Показывать рамки» запоминается между картами

        title = label("Обучение OCR", h1=True)
        self.stats = label("", muted=True)
        add_b = button("Добавить фото карт…", primary=True, on_click=self.add_dialog)
        clients_b = button("Взять из клиентов программы", on_click=self.import_clients)
        clients_b.setToolTip("Фото лица и оборота клиентов с уже проверенными ФИО, номером и адресом")
        self.train_b = button("Обучить модель…", on_click=self.train)
        self.train_b.setToolTip("Дообучить модели распознавания на проверенных картах (офлайн)")
        intro = wrap_label(
            "Перетащите сюда фото ID-карт (лицо и оборот вперемешку). Программа прочитает каждую — сверьте с фото, "
            "исправьте ошибки и нажмите «Верно ✓» (Enter). Клиенты не создаются: это учебная база, она хранится "
            "только на этом компьютере. Проверенные ФИО сразу пополняют словарь имен — он исправляет похожие "
            "ошибки при приемке.", muted=True)

        drop = DropZone("Перетащите фото карт", "можно сразу много — .jpg / .png / .heic, или папку",
                        training.IMAGE_EXTENSIONS, "Фото (*.jpg *.jpeg *.png *.heic *.webp *.bmp)", compact=True)
        drop.files_dropped.connect(self.add_files)
        self.filter_group = QButtonGroup(self)
        chips = hbox(spacing=6)
        for i, (text, value) in enumerate(FILTERS):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setProperty("chip", True)
            b.setProperty("value", value)
            b.setStyleSheet("padding:0 10px;min-width:0;")
            self.filter_group.addButton(b, i)
            chips.addWidget(b)
        chips.addStretch(1)
        self.filter_group.button(0).setChecked(True)
        self.filter_group.buttonClicked.connect(self._filter_changed)
        self.list = QListWidget()
        self.list.setStyleSheet("QListWidget::item{padding:0;}")
        self.list.currentRowChanged.connect(self._row_changed)
        left = vbox(drop, chips, self.list, spacing=10)
        lw = QWidget()
        lw.setLayout(left)
        lw.setFixedWidth(330)

        self.panel = frame("panel")
        self.panel_lay = vbox(spacing=10, margins=(18, 16, 18, 16))
        self.panel.setLayout(self.panel_lay)

        body = QGridLayout()
        body.setSpacing(16)
        body.addWidget(lw, 0, 0)
        body.addWidget(self.panel, 0, 1)
        body.setColumnStretch(1, 1)
        self.setLayout(vbox(hbox(title, None, self.stats, clients_b, add_b, self.train_b, spacing=12), intro, body,
                            spacing=12, margins=(32, 24, 32, 20)))
        self.timer = QTimer(self)  # обновление списка, пока идет OCR новых карт
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self.refresh_list)
        self.refresh()

    # ------------------------------------------------------------ данные

    def refresh(self):
        self.refresh_list()
        if self.current is None:
            self._select_next()

    def _cards(self):
        return self.store.cards(self.filter)

    def refresh_list(self):
        cur_id = self.current.id if self.current else None
        cards = self._cards()
        self.list.blockSignals(True)
        self.list.clear()
        row = -1
        for i, c in enumerate(cards):
            text, color = STATUS_TEXT.get(c.status, (c.status, "#8C8C8C"))
            title = c.name if c.side == training.FRONT and c.name else (
                c.address[:28] if c.side == training.BACK and c.address else c.original_name)
            it = QListWidgetItem(f"{SIDE_TEXT.get(c.side, '?')} · {title}\n{text}")
            it.setForeground(Qt.white if c.status == training.READY else Qt.gray)
            it.setData(Qt.UserRole, c.id)
            it.setSizeHint(QSize(0, 46))
            self.list.addItem(it)
            if c.id == cur_id:
                row = i
        self.list.setCurrentRow(row)
        self.list.blockSignals(False)
        st = self.store.stats()
        self.stats.setText(f"карт: {st['total']} · проверено: {st[training.DONE]} "
                           f"(лицо {st['front']}, оборот {st['back']}) · проверить: {st[training.READY]}"
                           + (f" · ждут OCR: {st[training.NEW]}" if st[training.NEW] else ""))
        if not st[training.NEW] and not self.ocr_running:
            self.timer.stop()

    def _filter_changed(self, b):
        self.save_draft()
        self.filter = b.property("value")
        self.current = None
        self.refresh_list()
        self._select_next()

    def _row_changed(self, row):
        if row < 0:
            return
        card_id = self.list.item(row).data(Qt.UserRole)
        self.show_card(self.store.card(card_id))

    def _select_next(self):
        """Следующая карта, которую нужно проверить."""
        nxt = next((c for c in self.store.cards(training.READY)), None)
        if nxt is None and self.filter:
            nxt = next(iter(self._cards()), None)
        self.show_card(nxt)
        self.refresh_list()

    # ------------------------------------------------------------ добавление

    def add_dialog(self):
        from PySide6.QtWidgets import QFileDialog
        files, _ = QFileDialog.getOpenFileNames(self, "Фото ID-карт", "",
                                                "Фото (*.jpg *.jpeg *.png *.heic *.webp *.bmp)")
        if files:
            self.add_files([Path(f) for f in files])

    def add_files(self, files):
        photos = [f for f in files if Path(f).suffix.lower() in training.IMAGE_EXTENSIONS]
        ids = self.store.add_images(photos)
        skipped = len(photos) - len(ids)
        self.state.busy_changed.emit(f"Добавлено фото: {len(ids)}" + (f", повторы пропущены: {skipped}" if skipped else ""))
        if skipped:
            QMessageBox.information(self, "Обучение OCR", f"Добавлено фото: {len(ids)}\nПропущено повторов: {skipped} "
                                    "(эти фото уже есть в учебной базе — разметка у них сохранена)")
        self.refresh_list()
        self.run_ocr()

    def import_clients(self):
        n = self.store.import_clients(self.state.db)
        QMessageBox.information(self, "Обучение OCR", f"Добавлено из клиентов программы: {n}"
                                if n else "Новых фото у клиентов нет — все уже в учебной базе.")
        self.refresh()

    def run_ocr(self):
        if self.ocr_running:
            return
        self.ocr_running = True
        self.timer.start()

        def done(_n):
            self.ocr_running = False
            self.state.busy_changed.emit("")
            self.refresh_list()
            if self.current is None or self.current.status == training.NEW:
                self._select_next()

        def failed(err):
            self.ocr_running = False
            self.state.busy_changed.emit("")
            QMessageBox.warning(self, "Обучение OCR", err)

        run_task(training.predict_pending, self.store, on_done=done, on_fail=failed,
                 on_progress=lambda m: self.state.busy_changed.emit(f"Обучение: {m}"))

    # ------------------------------------------------------------ разметка

    def show_card(self, card):
        self.save_draft()
        self.editing = None
        self.current = card
        clear_layout(self.panel_lay)
        if card is None:
            msg = wrap_label("Здесь появится фото карты для проверки.\n\nДобавьте фото карт кнопкой выше или "
                             "перетащите их в окно.", muted=True)
            msg.setAlignment(Qt.AlignCenter)
            self.panel_lay.addStretch(1)
            self.panel_lay.addWidget(msg)
            self.panel_lay.addStretch(1)
            return
        if card.status == training.NEW:
            self.panel_lay.addWidget(label(card.original_name, h2=True))
            self.panel_lay.addWidget(label("OCR еще читает эту карту…", muted=True))
            self.panel_lay.addStretch(1)
            return

        view = RegionView()
        view.show_boxes = self.show_boxes
        self.view = view
        self.rotation = card.rotation or 0
        self._show_photo(card.region_map)

        # сторона карты
        self.side = card.side if card.side in (training.FRONT, training.BACK) else training.FRONT
        side_group = QButtonGroup(self.panel)
        seg = hbox(spacing=6)
        for value, text in ((training.FRONT, "Лицо"), (training.BACK, "Оборот"), (training.OTHER, "Не карта")):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setProperty("chip", True)
            b.setChecked(value == (card.side or training.FRONT))
            b.clicked.connect(lambda _=False, v=value: self._set_side(v))
            side_group.addButton(b)
            seg.addWidget(b)
        seg.addStretch(1)
        status_text, color = STATUS_TEXT.get(card.status, ("", "#8C8C8C"))
        pill = label(status_text)
        pill.setStyleSheet(f"background:{color};color:#121212;border-radius:9px;padding:1px 10px;font-weight:800;")
        seg.addWidget(pill)

        def field_label(text, field):
            b = button("⌖ область", small=True, flat=True, on_click=lambda: self.view.mark(field))
            b.setToolTip("Поставить рамку поля заново — потом потяните за углы")
            return hbox(label(text, dim=True), None, b, spacing=6)

        big = "font-size:18px;font-weight:700;padding:6px 10px;"
        self.f_name = QLineEdit(card.name)
        self.f_name.setPlaceholderText("ФИО латиницей, как на карте")
        self.f_name.setStyleSheet(big)
        self.f_arc = QLineEdit(card.arc)
        self.f_arc.setPlaceholderText("Номер ARC: 900101-5123456")
        self.f_arc.setStyleSheet(big + "font-family:Consolas,monospace;")
        self.f_country = QLineEdit(card.country)
        self.f_country.setPlaceholderText("Страна (англ.)")
        self.f_address = QLineEdit(card.address)
        self.f_address.setPlaceholderText("Последний адрес (체류지) с оборота")
        self.f_address.setStyleSheet(big)
        for f in (self.f_name, self.f_arc, self.f_country, self.f_address):
            f.returnPressed.connect(self.confirm)

        self.front_box = QWidget()
        self.front_box.setLayout(vbox(field_label("ФИО", "name"), self.f_name,
                                      hbox(vbox(field_label("Номер ARC", "arc"), self.f_arc, spacing=4),
                                           vbox(field_label("Страна", "country"), self.f_country, spacing=4),
                                           spacing=10),
                                      spacing=4))
        self.back_box = QWidget()
        self.back_box.setLayout(vbox(field_label("Адрес (последний)", "address"), self.f_address, spacing=4))
        ocr_note = ""
        if card.pred_name or card.pred_arc or card.pred_address:
            ocr_note = "OCR прочитал: " + " · ".join(x for x in (card.pred_name, card.pred_arc, card.pred_address) if x)
        self.panel_lay.addLayout(hbox(label(card.original_name, h2=True), None, spacing=10))
        self.panel_lay.addLayout(seg)
        self.panel_lay.addWidget(self.front_box)
        self.panel_lay.addWidget(self.back_box)
        if ocr_note:
            note = wrap_label(ocr_note, dim=True)
            self.panel_lay.addWidget(note)
        self.panel_lay.addWidget(view, 1)
        rot_b = button("⟳ Повернуть фото", small=True, on_click=self.rotate_photo)
        self.boxes_cb = QCheckBox("Показывать рамки")
        self.boxes_cb.setChecked(self.show_boxes)
        self.boxes_cb.toggled.connect(self._toggle_boxes)
        view.boxes_toggled = lambda on: (self.boxes_cb.blockSignals(True), self.boxes_cb.setChecked(on),
                                         self.boxes_cb.blockSignals(False), setattr(self, "show_boxes", on))
        self.panel_lay.addLayout(hbox(
            self.boxes_cb,
            label("колесико мыши — увеличить / уменьшить под курсором · перетащить — сдвинуть · "
                  "двойной щелчок — целиком", dim=True), None, rot_b, spacing=12))
        ok_b = button("Верно ✓  (Enter)", primary=True, on_click=self.confirm)
        skip_b = button("Пропустить", on_click=self.skip)
        del_b = button("Удалить", on_click=self.delete)
        del_b.setStyleSheet(f"QPushButton{{border-color:{theme.ACCENT};color:{theme.ACCENT};}}")
        self.panel_lay.addLayout(hbox(del_b, None, skip_b, ok_b))
        self._set_side(card.side if card.side in (training.FRONT, training.BACK, training.OTHER) else training.FRONT)
        (self.f_address if self.side == training.BACK else self.f_name).setFocus()
        self.editing = card.id

    def save_draft(self):
        """Исправления на карте не теряются, если уйти с нее без «Верно» (другая карта, фильтр, страница)."""
        c = self.current
        if c is None or self.editing != c.id or self.side not in (training.FRONT, training.BACK):
            return
        regions = self.view.regions()
        if self.side == training.FRONT:
            self.store.save_draft(c.id, training.FRONT, name=self.f_name.text().upper(), arc=self.f_arc.text(),
                                  country=self.f_country.text().upper(), rotation=self.rotation,
                                  regions={k: v for k, v in regions.items() if k in training.FIELDS_FRONT})
        else:
            self.store.save_draft(c.id, training.BACK, address=self.f_address.text(), rotation=self.rotation,
                                  regions={k: v for k, v in regions.items() if k in training.FIELDS_BACK})

    def hideEvent(self, e):
        self.save_draft()
        super().hideEvent(e)

    def _show_photo(self, regions: dict):
        try:
            img = load_image(self.current.path)
            if self.rotation:
                img = img.rotate(self.rotation, expand=True)
            img.thumbnail((2400, 2400))
            self.view.set_pixmap(pil_to_pixmap(img))
            self.view.set_regions(regions)
        except Exception as e:  # noqa: BLE001
            self.view.set_text(f"Не удалось открыть фото: {e}")

    def _toggle_boxes(self, on: bool):
        self.show_boxes = on
        self.view.set_boxes_visible(on)

    def rotate_photo(self):
        """Повернуть фото на 90° по часовой; рамки после поворота ставятся заново."""
        self.rotation = (self.rotation - 90) % 360
        self._show_photo({})

    def _set_side(self, side):
        self.side = side
        self.front_box.setVisible(side == training.FRONT)
        self.back_box.setVisible(side == training.BACK)

    def confirm(self):
        c = self.current
        if c is None or c.status == training.NEW:
            return
        if self.side == training.FRONT and not self.f_name.text().strip():
            QMessageBox.information(self, "Обучение OCR", "Впишите ФИО — или отметьте «Не карта».")
            return
        if self.side == training.BACK and not self.f_address.text().strip():
            QMessageBox.information(self, "Обучение OCR", "Впишите адрес — или отметьте «Не карта».")
            return
        regions = self.view.regions()
        if self.side == training.FRONT:
            regions = {k: v for k, v in regions.items() if k in training.FIELDS_FRONT}
            self.store.save_label(c.id, training.FRONT, name=self.f_name.text().upper(), arc=self.f_arc.text(),
                                  country=self.f_country.text().upper(), rotation=self.rotation, regions=regions)
        elif self.side == training.BACK:
            regions = {k: v for k, v in regions.items() if k in training.FIELDS_BACK}
            self.store.save_label(c.id, training.BACK, address=self.f_address.text(), rotation=self.rotation,
                                  regions=regions)
        else:
            self.store.save_label(c.id, training.OTHER)
        self._advance()

    def skip(self):
        if self.current:
            self.store.skip(self.current.id)
            self._advance()

    def delete(self):
        if self.current and QMessageBox.question(self, "Удалить", "Удалить это фото из учебной базы?",
                                                 QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.store.delete(self.current.id)
            self.current = None
            self._advance()

    def _advance(self):
        self.current = None
        self._select_next()

    def train(self):
        from .train_dialog import TrainDialog
        self.save_draft()
        TrainDialog(self.store, self).exec()
