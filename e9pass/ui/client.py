"""Шаг 2: карточка клиента — профиль, документы, очередь Master-PDF."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QFrame, QGridLayout, QLabel, QLineEdit, QMessageBox,
                               QScrollArea, QSizePolicy, QWidget)

from .. import pdf_tools, services
from ..db import BUILTIN_A4, BUILTIN_BACK, BUILTIN_FRONT
from . import theme
from .widgets import (BusyBar, DropZone, NoWheelComboBox, badge, button, clear_layout, frame, hbox, label, open_path, run_task,
                      thumbnail, type_tag, vbox, wrap_label)

DOC_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".heic", ".bmp", ".tif", ".tiff", ".webp"}


class ClientPage(QWidget):
    def __init__(self, state):
        super().__init__()
        self.setObjectName("Page")
        self.state = state
        self.client_id = None
        self.inputs = {}

        self.name = label("", h1=True)
        self.badge_host = hbox()
        self.crumb = label("", muted=True)
        back = button("←  Назад", on_click=lambda: self.state.navigate.emit("dashboard"))
        back.setToolTip("Вернуться к списку клиентов")
        back.setStyleSheet("QPushButton{font-size:15px;font-weight:700;min-height:40px;padding:0 18px;}")
        self.btn_folder = button("Открыть папку", on_click=lambda: open_path(self.client().folder))
        self.btn_report = button("Отчет OCR", on_click=self.ocr_report)
        self.btn_report.setToolTip("Строки, которые увидел OCR, и что из них взято — чтобы отправить разработчику")
        self.btn_zip = button("Экспорт ZIP клиента", on_click=self.export_zip)
        self.btn_master = button("Собрать Master-PDF", primary=True, on_click=self.build_master)
        self.btn_delete = button("Удалить клиента", on_click=self.delete_client)
        self.btn_delete.setStyleSheet(f"QPushButton{{border-color:{theme.ACCENT};color:{theme.ACCENT};}}")
        self.btn_delete.setToolTip("Удалить клиента и все его файлы из программы")
        header = hbox(vbox(hbox(back, self.crumb, None, spacing=12), hbox(self.name, self.badge_host, None, spacing=14),
                           spacing=2), None, self.btn_delete, self.btn_report, self.btn_folder, self.btn_zip, self.btn_master)

        # --- профиль
        self.profile = frame("panel")
        self.profile.setFixedWidth(400)
        # --- документы
        self.docs_panel = frame("panel")
        self.docs_lay = vbox(spacing=8)
        docs_host = QWidget()
        docs_host.setLayout(self.docs_lay)
        docs_scroll = QScrollArea()
        docs_scroll.setWidgetResizable(True)
        docs_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        docs_scroll.setWidget(docs_host)
        self.docs_title = label("ДОКУМЕНТЫ", caps=True)
        drop = DropZone("Перетащите документы в любое место окна", "или нажмите здесь, чтобы выбрать файлы",
                        DOC_EXTENSIONS, "Документы (*.pdf *.jpg *.jpeg *.png *.heic);;Все файлы (*)", compact=True,
                        accept_fn=lambda f: pdf_tools.real_extension(f) in pdf_tools.KNOWN_EXTENSIONS)
        drop.files_dropped.connect(self.add_files)
        self.drop = drop
        self.btn_recheck = button("Определить заново", small=True, on_click=self.recheck)
        self.btn_recheck.setToolTip("Еще раз: тип файла, пароль (дата рождения), тип документа по маске и OCR")
        self.busy = BusyBar()
        self.docs_panel.setLayout(vbox(hbox(self.docs_title, None, self.btn_recheck),
                                       self.busy, docs_scroll, drop, spacing=10, margins=(18, 16, 18, 16)))
        # --- очередь Master-PDF
        self.queue_panel = frame("panel")
        self.queue_panel.setFixedWidth(330)
        self.queue_lay = vbox(spacing=8, margins=(18, 16, 18, 16))
        self.queue_panel.setLayout(self.queue_lay)

        body = QGridLayout()
        body.setSpacing(16)
        body.addWidget(self.profile, 0, 0)
        body.addWidget(self.docs_panel, 0, 1)
        body.addWidget(self.queue_panel, 0, 2)
        body.setColumnStretch(1, 1)
        self.setLayout(vbox(header, body, spacing=18, margins=(32, 24, 32, 20)))
        state.data_changed.connect(lambda: self.client_id and self.isVisible() and self.refresh())

    def client(self):
        return self.state.db.client(self.client_id)

    def open(self, client_id: int):
        self.client_id = client_id
        self.refresh()

    # ------------------------------------------------------------ отрисовка

    def refresh(self):
        c = self.client()
        if not c:
            self.state.navigate.emit("dashboard")
            return
        db = self.state.db
        docs = db.documents(c.id)
        self.name.setText(c.full_name or "(без имени)")
        clear_layout(self.badge_host)
        self.badge_host.addWidget(badge(c.status))
        self.crumb.setText(f"/ {c.batch_period}")
        self._render_profile(c, docs)
        self._render_docs(c, docs)
        self._render_queue(c)

    def _render_profile(self, c, docs):
        # профиль внутри прокрутки — на невысоком экране поля не налезают друг на друга
        if self.profile.layout() is None:
            self.profile_scroll = QScrollArea()
            self.profile_scroll.setWidgetResizable(True)
            self.profile_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.profile.setLayout(vbox(self.profile_scroll, margins=(0, 0, 0, 0)))
        old = self.profile_scroll.takeWidget()
        if old:
            old.deleteLater()
        host = QWidget()
        lay = vbox(spacing=10, margins=(18, 16, 14, 16))
        lay.addWidget(label("ПРОФИЛЬ (ИЗ OCR, МОЖНО ПРАВИТЬ)", caps=True))
        thumbs = hbox(spacing=10)
        for code, text in ((BUILTIN_FRONT, "лицо"), (BUILTIN_BACK, "оборот")):
            d = next((x for x in docs if x.doc_type == code and not x.is_processed_scan), None)
            t = QLabel(text)
            t.setFixedSize(110, 70)
            t.setAlignment(Qt.AlignCenter)
            t.setStyleSheet("border:1px solid rgba(255,255,255,0.3);border-radius:8px;color:#8C8C8C;")
            if d:
                pm = thumbnail(d.path, 108)
                if pm:
                    t.setPixmap(pm)
                t.setCursor(Qt.PointingHandCursor)
                if code == BUILTIN_FRONT:
                    t.setToolTip("Редактор скана: углы карты, лист А4, проверка ФИО")
                    t.mousePressEvent = lambda _e, doc=d: self.open_scan_editor(doc)
                else:  # оборот: крупный просмотр и правка адреса, скан для него не нужен
                    t.setToolTip("Оборот крупно и правка адреса")
                    t.mousePressEvent = lambda _e, doc=d: self.open_back_viewer(doc)
            thumbs.addWidget(t)
        thumbs.addWidget(label("Нажмите на фото —\nпроверка и правка", dim=True))
        thumbs.addStretch(1)
        lay.addLayout(thumbs)

        self.inputs = {}

        def field(key, title, value, mono=False, warn=False):
            e = QLineEdit(value)
            if mono:
                e.setProperty("mono", True)
                e.setStyleSheet("font-family: Consolas, 'JetBrains Mono', monospace;")
            if warn:
                e.setProperty("warn", True)
            self.inputs[key] = e
            return vbox(label(title, dim=True), e, spacing=4)

        lay.addLayout(field("full_name", "ФИО", c.full_name, warn=not c.full_name))
        lay.addLayout(hbox(field("arc_number", "Номер ARC", c.arc_number, True, not c.arc_number),
                           field("birth_date", "Дата рождения (YYMMDD)", c.birth_date, True)))
        lay.addLayout(field("nationality", "Гражданство (англ.)", c.nationality, warn=not c.nationality))
        lay.addLayout(field("registered_address", "Адрес регистрации (체류지, с оборота)",
                            c.registered_address, warn=not c.registered_address))
        lay.addLayout(hbox(field("phone", "Телефон", c.phone, True), field("email", "E-mail", c.email)))

        bank = QFrame()
        bank.setObjectName("BankBox")
        bank.setStyleSheet("QFrame#BankBox{border:1px dashed rgba(255,255,255,0.3);border-radius:10px;}")
        self.inputs["bank_name"] = QLineEdit(c.bank_name)
        self.inputs["account_number"] = QLineEdit(c.account_number)
        self.inputs["account_number"].setEchoMode(QLineEdit.Password)
        eye = button("👁", flat=True)
        eye.setFixedWidth(36)
        eye.setToolTip("Показать / скрыть счет")
        eye.clicked.connect(lambda: self.inputs["account_number"].setEchoMode(
            QLineEdit.Normal if self.inputs["account_number"].echoMode() == QLineEdit.Password else QLineEdit.Password))
        bank.setLayout(vbox(label("БАНК · ТОЛЬКО ВНУТРИ ПРОГРАММЫ", caps=True),
                            hbox(self.inputs["bank_name"], spacing=6),
                            hbox(self.inputs["account_number"], eye, spacing=6),
                            label("Не попадает в Excel-реестр", dim=True), spacing=6, margins=(12, 10, 12, 10)))
        lay.addWidget(bank)
        if c.warnings:
            lay.addWidget(wrap_label("⚠ " + "; ".join(c.warnings), warn=True))
        lay.addStretch(1)
        lay.addLayout(hbox(None, button("Сохранить", primary=True, on_click=self.save_profile)))
        host.setLayout(lay)
        self.profile_scroll.setWidget(host)

    def _render_docs(self, c, docs):
        clear_layout(self.docs_lay)
        db = self.state.db
        rules = db.rules()
        names = {r.code: r.doc_name for r in rules}
        self.docs_title.setText(f"ДОКУМЕНТЫ КЛИЕНТА · {len(docs)}")
        for d in docs:
            row = QFrame()
            row.setObjectName("DocRow")
            locked = d.password_status == "locked"
            border = f"1px solid {theme.ACCENT}" if locked or not d.doc_type else "1px solid rgba(255,255,255,0.12)"
            row.setStyleSheet("QFrame#DocRow{background:#181818;border-radius:10px;border:%s;}" % border)
            line1 = []
            if d.doc_type == BUILTIN_A4 or d.is_processed_scan:
                line1.append(type_tag(names.get(d.doc_type, d.doc_type) + (" · скан" if d.is_processed_scan and
                                                                          d.doc_type != BUILTIN_A4 else "")))
            else:
                combo = NoWheelComboBox()
                combo.addItem("— не определен —", "")
                for r in rules:
                    if r.code != BUILTIN_A4:
                        combo.addItem(r.doc_name, r.code)
                combo.setCurrentIndex(max(0, combo.findData(d.doc_type)))
                combo.setFixedWidth(200)
                if not d.doc_type:
                    combo.setProperty("warn", True)
                combo.currentIndexChanged.connect(lambda _i, cb=combo, doc=d: self.change_type(doc.id, cb.currentData()))
                line1.append(combo)
            fname = button(d.path.name, flat=True, on_click=lambda _=False, p=d.path: open_path(p))
            fname.setStyleSheet("text-align:left;color:#E6E6E6;font-weight:500;")
            fname.setToolTip(f"Открыть файл: {d.path.name}\nИсходное имя: {d.original_name}")
            fname.setMinimumWidth(40)
            fname.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            line1 += [fname]
            scan_status = None
            actions = []
            if d.doc_type == BUILTIN_FRONT and not d.is_processed_scan:
                done = self._scan_result(docs, d.doc_type)
                if done is not None:
                    manual = done.detected_by == services.SCAN_MANUAL
                    scan_status = "✓ скан обработан" if manual else "скан создан автоматически — проверьте"
                    actions.append(button("Посмотреть скан", small=True,
                                          on_click=lambda _=False, p=done.path: self.preview(p, "Обработанный скан")))
                    actions.append(button("Изменить скан", small=True,
                                          on_click=lambda _=False, doc=d: self.open_scan_editor(doc)))
                else:
                    actions.append(button("Редактор скана", primary=True, small=True,
                                          on_click=lambda _=False, doc=d: self.open_scan_editor(doc)))
            elif d.doc_type == BUILTIN_BACK and not d.is_processed_scan:
                actions.append(button("Посмотреть и адрес", small=True,
                                      on_click=lambda _=False, doc=d: self.open_back_viewer(doc)))
            else:
                actions.append(button("Посмотреть", small=True, on_click=lambda _=False, p=d.path: self.preview(p)))
            rm = button("✕", flat=True, on_click=lambda _=False, doc=d: self.delete_doc(doc))
            rm.setToolTip("Убрать документ из клиента")
            rm.setFixedWidth(32)
            line1.append(rm)
            if locked:
                tried = " и ".join(pdf_tools.birth_passwords(c.birth_date, c.arc_number)) or "дата рождения"
                st = wrap_label(f"Пароль {tried} не подошел — введите вручную", warn=True)
                st.setMinimumWidth(10)
                pw = QLineEdit()
                pw.setPlaceholderText("Пароль")
                pw.setFixedWidth(120)
                pw.setProperty("warn", True)
                ok = button("Снять", primary=True, small=True)
                ok.clicked.connect(lambda _=False, doc=d, e=pw: self.unlock(doc.id, e.text()))
                pw.returnPressed.connect(lambda doc=d, e=pw: self.unlock(doc.id, e.text()))
                line2 = hbox(st, pw, ok, spacing=8)
            else:
                info = vbox(spacing=2)
                reason = label(d.detected_by or "", dim=True)
                reason.setMinimumWidth(10)
                info.addWidget(reason)
                if scan_status:
                    st = label(scan_status, warn=not scan_status.startswith("✓"))
                    if scan_status.startswith("✓"):
                        st.setStyleSheet(f"color:{theme.ACCENT};font-weight:700;")
                    info.addWidget(st)
                line2 = hbox(info, None, *actions, spacing=8)
            row.setLayout(vbox(hbox(*line1, spacing=10), line2, spacing=6, margins=(10, 6, 10, 8)))
            row.setToolTip("Двойной щелчок — посмотреть документ")
            row.mouseDoubleClickEvent = lambda _e, p=d.path: self.preview(p)
            self.docs_lay.addWidget(row)
        self.docs_lay.addStretch(1)

    def _render_queue(self, c):
        clear_layout(self.queue_lay)
        self.queue_lay.addWidget(label("ОЧЕРЕДЬ MASTER-PDF", caps=True))
        self.queue_lay.addWidget(wrap_label("Порядок — из «Типов документов». Нет документа — пропуск.", dim=True))
        db = self.state.db
        total = 0
        all_docs = db.documents(c.id)
        for n, (rule, docs) in enumerate(services.master_queue(db, c.id), start=1):
            pages = sum(pdf_tools.page_count(d.path) for d in docs)
            total += pages
            locked = any(d.doc_type == rule.code and d.password_status == "locked" for d in all_docs)
            num = QLabel(str(n))
            num.setFixedSize(24, 24)
            num.setAlignment(Qt.AlignCenter)
            if docs:
                num.setStyleSheet("background:#FFFFFF;color:#121212;border-radius:12px;font-weight:800;font-size:12px;")
                right = label(f"{pages} стр.", muted=True)
                css = "border:1px solid rgba(255,255,255,0.14);"
            else:
                num.setStyleSheet("background:#3A3A3A;color:#B3B3B3;border-radius:12px;font-weight:800;font-size:12px;")
                right = label("ждет пароль" if locked else "нет · пропуск", warn=locked, dim=not locked)
                css = f"border:1px dashed {theme.ACCENT if locked else '#5C5C5C'};"
            row = QFrame()
            row.setStyleSheet(f"QFrame{{{css}border-radius:10px;}} QLabel{{border:none;}}")
            name = label(rule.doc_name)
            if not docs:
                name.setStyleSheet(f"color:{theme.ACCENT if locked else theme.DIM};")
            row.setLayout(hbox(num, name, None, right, spacing=10, margins=(10, 8, 10, 8)))
            self.queue_lay.addWidget(row)
        link = button("Изменить порядок и типы →", flat=True, on_click=lambda: self.state.navigate.emit("doctypes"))
        link.setStyleSheet(f"color:{theme.ACCENT};text-align:left;font-weight:600;")
        self.queue_lay.addWidget(link)
        self.queue_lay.addStretch(1)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background:rgba(255,255,255,0.1);")
        self.queue_lay.addWidget(line)
        self.queue_lay.addLayout(hbox(label("Итого", muted=True), None, label(f"<b>{total} стр.</b>")))
        mp = Path(c.master_pdf) if c.master_pdf else None
        if mp and mp.exists():
            b = button(mp.name, flat=True, on_click=lambda: open_path(mp))
            b.setStyleSheet("text-align:left;font-family:Consolas,monospace;font-size:12px;color:#E6E6E6;")
            self.queue_lay.addWidget(b)
        else:
            self.queue_lay.addWidget(label("Master-PDF еще не собран", dim=True))

    # ------------------------------------------------------------ действия

    def save_profile(self):
        values = {k: e.text().strip() for k, e in self.inputs.items()}
        c = self.client()
        if values.get("arc_number") and values["arc_number"] != c.arc_number:
            recent, _old = services.recent_duplicates(self.state.db, values["arc_number"], exclude_id=c.id)
            if recent:
                where = "\n".join(f"• {services.describe_client(x)}" for x in recent)
                if QMessageBox.warning(self, "Такой ARC уже есть",
                                       f"Клиент с этим номером ARC добавлен меньше года назад:\n\n{where}\n\n"
                                       "Все равно сохранить? (прежнюю запись можно удалить на дашборде)",
                                       QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                    return
        services.set_client_fields(self.state.db, self.client_id, **values)
        self.state.data_changed.emit()
        self.state.busy_changed.emit("Сохранено")

    def change_type(self, doc_id, code):
        self.state.db.update_document(doc_id, doc_type=code, detected_by="выбрано вручную")
        services._refresh_status(self.state.db, self.client_id)
        self.state.data_changed.emit()

    def delete_doc(self, doc):
        if QMessageBox.question(self, "Убрать документ", f"Убрать «{doc.path.name}» из клиента?\n"
                                "Файл останется в папке клиента.") == QMessageBox.Yes:
            self.state.db.delete_document(doc.id)
            services._refresh_status(self.state.db, self.client_id)
            self.state.data_changed.emit()

    def delete_client(self):
        c = self.client()
        if confirm_delete_client(self, c):
            services.delete_client_everything(self.state.db, c.id)
            self.client_id = None
            self.state.data_changed.emit()
            self.state.navigate.emit("dashboard")

    def ocr_report(self):
        c = self.client()
        if not c:
            return
        self._busy("Готовлю отчет OCR…", "распознаю фото заново")

        def done(text):
            self._idle()
            from .ocr_report import OcrReportDialog
            OcrReportDialog(text, c.full_name, self).exec()

        run_task(services.ocr_report, self.state.db, c.id, on_done=done, on_progress=self._progress,
                 on_fail=lambda err: (self._idle(), QMessageBox.warning(self, "Отчет OCR", err)))

    def _busy(self, title, on_progress_text=""):
        self.busy.start(title, on_progress_text)
        for w in (self.btn_master, self.btn_recheck, self.btn_zip, self.btn_delete, self.btn_report, self.drop):
            w.setEnabled(False)

    def _idle(self):
        self.busy.stop()
        for w in (self.btn_master, self.btn_recheck, self.btn_zip, self.btn_delete, self.btn_report, self.drop):
            w.setEnabled(True)
        self.state.busy_changed.emit("")

    def _progress(self, text):
        self.busy.set_text(text)
        self.state.busy_changed.emit(text)

    def recheck(self):
        self._busy("Определяю документы заново…")
        run_task(services.recheck_documents, self.state.db, self.client_id, on_progress=self._progress,
                 on_done=lambda n: (self._idle(), self.state.data_changed.emit(),
                                    QMessageBox.information(self, "Готово", f"Определено документов: {n}")),
                 on_fail=lambda err: (self._idle(), QMessageBox.warning(self, "Ошибка", err)))

    def add_files(self, files):
        cid = self.client_id
        self._busy(f"Добавляю документы ({len(files)})…", "загрузка модели распознавания")
        run_task(services.add_documents, self.state.db, cid, files, on_progress=self._progress,
                 on_done=lambda _r: (self._idle(), self.state.data_changed.emit()),
                 on_fail=lambda err: (self._idle(), self.state.data_changed.emit(),
                                      QMessageBox.warning(self, "Ошибка", err)))

    def unlock(self, doc_id, password):
        if not password:
            return
        if services.unlock_with_password(self.state.db, doc_id, self.client_id, password):
            self.state.data_changed.emit()
        else:
            QMessageBox.warning(self, "Пароль не подошел", "Этот пароль не подходит к файлу.")

    def build_master(self):
        c = self.client()
        old = Path(c.master_pdf) if c and c.master_pdf else None
        if old and old.exists():
            if QMessageBox.question(self, "Master-PDF", f"Master-PDF уже собран:\n{old.name}\n\n"
                                    "Собрать заново? Старый файл будет удален.") != QMessageBox.Yes:
                return
            try:
                old.unlink()
            except OSError:
                QMessageBox.warning(self, "Master-PDF", f"Не удалось удалить {old.name}.\n"
                                    "Закройте его в программе просмотра PDF и попробуйте еще раз.")
                return
            self.state.db.update_client(c.id, master_pdf="")
            self.state.data_changed.emit()
        self._busy("Собираю Master-PDF…", "проверяю пароли")
        run_task(services.build_master, self.state.db, self.client_id, on_progress=self._progress,
                 on_done=self._master_done,
                 on_fail=lambda err: (self._idle(), QMessageBox.warning(self, "Ошибка", err)))

    def _master_done(self, res):
        self._idle()
        self.state.data_changed.emit()
        problems = ""
        if res.locked:
            problems += "\n\n⚠ Не подошел пароль (введите вручную в списке документов):\n• " + "\n• ".join(res.locked)
        if res.unknown:
            problems += "\n\n⚠ Тип не определен — эти файлы НЕ попали в Master-PDF:\n• " + "\n• ".join(res.unknown)
        if not res.path:
            QMessageBox.information(self, "Master-PDF", "Нет документов для Master-PDF." + problems)
            return
        msg = f"Собран: {res.path.name}\n\nВключено: {', '.join(res.included)}"
        if res.skipped:
            msg += f"\nПропущено (нет у клиента): {', '.join(res.skipped)}"
        msg += problems
        box = QMessageBox(self)
        box.setWindowTitle("Master-PDF")
        box.setText(msg)
        open_b = box.addButton("Открыть", QMessageBox.AcceptRole)
        box.addButton("OK", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() == open_b:
            open_path(res.path)

    def export_zip(self):
        folder = QFileDialog.getExistingDirectory(self, "Куда сохранить ZIP клиента")
        if not folder:
            return
        out = services.export_client_zip(self.state.db, self.client_id, Path(folder))
        self.state.data_changed.emit()
        QMessageBox.information(self, "Готово", f"Архив сохранен:\n{out}")

    @staticmethod
    def _scan_result(docs, doc_type):
        """Обработанный скан для фото ID: лист А4 для лица, скан оборота для оборота."""
        if doc_type == BUILTIN_FRONT:
            return next((x for x in docs if x.doc_type == BUILTIN_A4), None)
        return next((x for x in docs if x.doc_type == BUILTIN_BACK and x.is_processed_scan), None)

    def preview(self, path, title=""):
        from .preview import show_preview
        show_preview(path, title or Path(path).name, self)

    def open_back_viewer(self, doc):
        from .back_viewer import BackViewer
        dlg = BackViewer(self.state, self.client_id, doc, self)
        dlg.exec()
        if dlg.changed:
            self.state.data_changed.emit()
            self.state.busy_changed.emit("Адрес сохранен")

    def open_scan_editor(self, doc):
        from .scan_editor import ScanEditor
        dlg = ScanEditor(self.state, self.client_id, doc, self)
        if dlg.exec():
            self.state.data_changed.emit()
            self.state.busy_changed.emit("Скан сохранен")
            if dlg.result_path:
                self.preview(dlg.result_path, "Скан сохранен — проверьте результат")


def confirm_delete_client(parent, c) -> bool:
    return QMessageBox.question(
        parent, "Удалить клиента",
        f"Удалить {c.full_name or 'клиента'} ({c.arc_number or 'без ARC'}) из периода {c.batch_period}?\n\n"
        "Будут удалены все данные клиента в программе и его папка с файлами "
        "(оригиналы, сканы, Master-PDF). Отменить нельзя.\n\n"
        "Архив, из которого он был загружен, не трогается — его можно загрузить заново.",
        QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes
