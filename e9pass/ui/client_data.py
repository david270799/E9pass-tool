"""Окно «Данные клиентов»: узкое, поверх окон — список → данные клиента (нажал на поле — скопировалось)."""

import re

from PySide6.QtCore import QEvent, QPoint, QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import (QCheckBox, QFrame, QLabel, QLineEdit, QListWidget, QListWidgetItem, QScrollArea,
                               QStackedWidget, QWidget)

from . import theme
from .widgets import STAGE_COLOR, breakable, button, clear_layout, hbox, label, stage_pill, vbox, wrap_label

# Номер и цвет в уголке каждого поля — чтобы различать их с одного взгляда (и копировать клавишей 1–8)
FIELD_COLORS = ["#F5C518", "#FF9F1C", "#4DA3FF", "#3DDC84", "#2EC4B6", "#B57BFF", "#FF6B9A", "#FF7A45"]


def split_phone(phone: str):
    """010-1234-5678 → ('1234', '5678'): код 010 в формы не вводят."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) in (10, 11) and digits.startswith("01"):
        return digits[3:-4], digits[-4:]
    return "", ""


def split_arc(arc: str):
    """900101-5123456 → ('900101', '5123456')."""
    digits = re.sub(r"\D", "", arc or "")
    return (digits[:6], digits[6:]) if len(digits) == 13 else ("", "")


class FadeLabel(QLabel):
    """Текст в одну строку: если не помещается — плавно уходит в тень у правого края (без «…» и переноса)."""

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.fontMetrics().horizontalAdvance(self.text()) <= self.width():
            return
        from PySide6.QtGui import QLinearGradient
        p = QPainter(self)
        w = min(36, self.width())
        g = QLinearGradient(self.width() - w, 0, self.width(), 0)
        g.setColorAt(0, QColor(18, 18, 18, 0))
        g.setColorAt(1, QColor(18, 18, 18, 255))
        p.fillRect(self.width() - w, 0, w, self.height(), g)
        p.end()


class CopyField(QFrame):
    """Поле-кнопка: номер с цветом в уголке, значение крупно; нажатие копирует значение."""

    def __init__(self, number: int, title: str, value: str, mono: bool = False, center: bool = False,
                 compact: bool = False):
        super().__init__()
        self.value = value
        color = FIELD_COLORS[(number - 1) % len(FIELD_COLORS)]
        self.color = color
        tag = QLabel(str(number))
        tag.setAlignment(Qt.AlignCenter)
        tag.setFixedSize(20, 20)
        tag.setStyleSheet(f"background:{color};color:#121212;border-radius:6px;font-weight:900;font-size:12px;")
        self.title = label(title, dim=True)
        self.done = label("✓" if compact else "✓ скопировано")
        self.done.setStyleSheet("color:#3DDC84;font-size:11.5px;font-weight:700;")
        self.done.hide()
        self.text = QLabel(breakable(value) if value else "—")
        self.text.setWordWrap(True)
        self.text.setAlignment(Qt.AlignCenter if center else Qt.AlignLeft | Qt.AlignVCenter)
        self.text.setStyleSheet("font-size:15px;font-weight:700;" + ("font-family:Consolas,monospace;font-size:16px;"
                                                                      if mono else "")
                                + ("" if value else f"color:{theme.DIM};"))
        self.setLayout(vbox(hbox(tag, self.title, None, self.done, spacing=6), self.text, spacing=4,
                            margins=(8, 6, 10, 8)))
        self.setCursor(Qt.PointingHandCursor if value else Qt.ArrowCursor)
        self.setToolTip(f"{number} — нажмите (или клавиша {number}), чтобы скопировать" if value else "Нет данных")
        self._style(False)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(lambda: (self._style(False), self.done.hide()))

    def _style(self, copied: bool):
        border = "#3DDC84" if copied else self.color + "66"
        self.setStyleSheet(f"CopyField{{background:#161616;border:1px solid {border};border-left:3px solid "
                           f"{'#3DDC84' if copied else self.color};border-radius:9px;}}"
                           "CopyField:hover{background:#1d1d1d;}")

    def copy(self):
        if not self.value:
            return
        QGuiApplication.clipboard().setText(self.value)
        self._style(True)
        self.done.show()
        self.timer.start(1500)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.copy()


class LogoBubble(QWidget):
    """Свернутое окно: круглый значок поверх всех окон. Перетаскивается; нажатие — окно обратно."""

    SIZE = 64

    def __init__(self, on_open):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        from .main_window import rounded_logo

        self.on_open = on_open
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.logo = rounded_logo(self.SIZE - 18)
        self.setToolTip("Данные клиентов — нажмите, чтобы открыть")
        self.setCursor(Qt.PointingHandCursor)
        self._press = None
        self._moved = False

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 90))
        p.drawEllipse(2, 4, self.SIZE - 4, self.SIZE - 4)
        p.setBrush(QColor(theme.ACCENT))
        p.drawEllipse(1, 1, self.SIZE - 4, self.SIZE - 4)
        p.drawPixmap(8, 8, self.logo)
        p.end()

    def mousePressEvent(self, e):
        self._press = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        self._moved = False

    def mouseMoveEvent(self, e):
        if self._press is not None and e.buttons() & Qt.LeftButton:
            pos = e.globalPosition().toPoint() - self._press
            if (pos - self.pos()).manhattanLength() > 3:
                self._moved = True
                self.move(pos)

    def mouseReleaseEvent(self, _e):
        if not self._moved:
            self.on_open()
        self._press = None


class ClientDataWindow(QWidget):
    """Узкое окно для заполнения форм: список клиентов месяца → нажал на клиента → его данные."""

    def __init__(self, state):
        super().__init__(None, Qt.Window)
        from .main_window import rounded_logo

        self.state = state
        self.client_id = None
        self.fields = []
        self.settings = QSettings("E9pay", "E9pay")
        self.setWindowTitle("Данные клиентов")
        self.setObjectName("Page")
        self.setMinimumWidth(300)
        self.resize(340, 660)
        self.bubble = LogoBubble(self.restore_from_bubble)

        # --- список
        logo = QLabel()
        logo.setPixmap(rounded_logo(26))
        title = label("Данные клиентов")
        title.setStyleSheet("font-weight:800;font-size:14px;")
        self.period_label = label("", dim=True)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по ФИО или ARC")
        self.search.textChanged.connect(self.refresh_list)
        self.list = QListWidget()
        self.list.setStyleSheet("QListWidget{background:transparent;border:none;outline:none;}"
                                "QListWidget::item{border-bottom:1px solid rgba(255,255,255,0.08);}"
                                "QListWidget::item:hover{background:rgba(255,255,255,0.06);}"
                                "QListWidget::item:selected{background:rgba(255,255,255,0.10);}")
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.viewport().installEventFilter(self)  # ширина изменилась — пересчитать высоту строк
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.list.itemClicked.connect(lambda it: self.open_client(it.data(Qt.UserRole)))
        self.list.itemActivated.connect(lambda it: self.open_client(it.data(Qt.UserRole)))
        list_page = QWidget()
        list_page.setLayout(vbox(hbox(logo, title, None, *self._window_buttons(), spacing=8), self.period_label,
                                 self.search, self.list, spacing=8, margins=(12, 10, 12, 10)))

        # --- данные клиента
        back = button("‹ Назад", flat=True, on_click=self.show_list)
        back.setStyleSheet(f"QPushButton{{color:{theme.ACCENT};font-weight:800;padding:0 4px;}}")
        back.setToolTip("Назад к списку (Esc)")
        self.name_label = label("")
        self.name_label.setStyleSheet("font-weight:800;font-size:14px;")
        self.name_label.setWordWrap(True)
        self.stage_host = hbox()
        self.fields_lay = vbox(spacing=7)
        fields_host = QWidget()
        fields_host.setLayout(vbox(self.fields_lay, None, spacing=0, margins=(0, 0, 2, 0)))
        scroll = QScrollArea()  # длинное ФИО / адрес или низкое окно — поля прокручиваются, а не обрезаются
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;}")
        scroll.viewport().setAutoFillBackground(False)
        fields_host.setAutoFillBackground(False)
        scroll.setWidget(fields_host)
        detail_page = QWidget()
        detail_page.setLayout(vbox(hbox(back, None, *self._window_buttons(), spacing=8),
                                   hbox(self.name_label, None, self.stage_host, spacing=8),
                                   wrap_label("Нажмите на поле — оно скопируется. Клавиши 1–8 — то же самое.",
                                              dim=True),
                                   scroll, spacing=8, margins=(12, 10, 12, 10)))

        self.stack = QStackedWidget()
        self.stack.addWidget(list_page)
        self.stack.addWidget(detail_page)
        self.setLayout(vbox(self.stack))
        self.set_on_top(self.settings.value("client_data/on_top", True, type=bool))
        state.data_changed.connect(self.refresh)
        state.period_changed.connect(lambda _p: (self.show_list(), self.refresh()))
        self.refresh()

    # ------------------------------------------------------------ окно
    def _window_buttons(self):
        pin = button("📌", flat=True, on_click=lambda: self.set_on_top(not self.on_top))
        pin.setFixedWidth(30)
        mini = button("—", flat=True, on_click=self.to_bubble)
        mini.setFixedWidth(30)
        mini.setToolTip("Свернуть до значка")
        if not hasattr(self, "pins"):
            self.pins = []
        self.pins.append(pin)
        return [pin, mini]

    def set_on_top(self, on: bool):
        self.on_top = on
        self.settings.setValue("client_data/on_top", on)
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowStaysOnTopHint, on)
        for pin in self.pins:
            pin.setToolTip("Поверх всех окон: включено" if on else "Поверх всех окон: выключено")
            pin.setStyleSheet("" if on else "QPushButton{color:#666;}")
        if visible:
            self.show()

    def to_bubble(self):
        g = self.frameGeometry()
        self.bubble.move(g.topRight() - QPoint(self.bubble.width(), 0))
        self.hide()
        self.bubble.show()

    def restore_from_bubble(self):
        b = self.bubble.frameGeometry()
        self.bubble.hide()
        self.move(b.topRight() - QPoint(self.frameGeometry().width(), 0))
        self.show_window()

    def show_window(self):
        self.bubble.hide()
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e):
        self.bubble.hide()
        super().closeEvent(e)

    def keyPressEvent(self, e):
        if self.stack.currentIndex() == 1:
            if e.key() in (Qt.Key_Escape, Qt.Key_Backspace):
                self.show_list()
                return
            if Qt.Key_1 <= e.key() <= Qt.Key_9:
                n = e.key() - Qt.Key_1
                if n < len(self.fields):
                    self.fields[n].copy()
                return
        super().keyPressEvent(e)

    # ------------------------------------------------------------ данные
    def refresh(self):
        self.refresh_list()
        if self.stack.currentIndex() == 1:
            if self.client_id and self.state.db.client(self.client_id):
                self.open_client(self.client_id)
            else:
                self.show_list()

    def refresh_list(self):
        clients = self.state.db.clients(self.state.period)
        self.period_label.setText(f"{self.state.period} · клиентов: {len(clients)}")
        q = self.search.text().strip().lower()
        if q:
            clients = [c for c in clients if q in c.full_name.lower() or q in c.arc_number]
        self.list.clear()
        for c in clients:
            it = QListWidgetItem()
            it.setData(Qt.UserRole, c.id)
            row = QWidget()
            name = FadeLabel(c.full_name or "(без имени)")
            name.setStyleSheet("font-weight:600;")
            name.setMinimumWidth(10)
            name.setToolTip(c.full_name)
            arc = label(c.arc_number or "—", dim=True)
            dot = QLabel("●")
            dot.setStyleSheet(f"color:{STAGE_COLOR.get(c.stage, theme.DIM)};font-size:14px;")
            row.setLayout(hbox(vbox(name, arc, spacing=4), dot, spacing=8, margins=(6, 9, 6, 9)))
            row.layout().setStretch(0, 1)
            row.setAttribute(Qt.WA_TransparentForMouseEvents)
            self.list.addItem(it)
            self.list.setItemWidget(it, row)
        self._fit_rows()

    def _fit_rows(self):
        """Строка — во всю ширину окна (точка этапа всегда видна), высота — по тексту."""
        width = self.list.viewport().width()
        for i in range(self.list.count()):
            it = self.list.item(i)
            row = self.list.itemWidget(it)
            if row is not None:
                row.setFixedWidth(width)
                for w in row.findChildren(QLabel):
                    w.ensurePolished()  # шрифт из стиля — до измерения, иначе высота занижена
                it.setSizeHint(QSize(width, row.sizeHint().height() + 2))

    def eventFilter(self, obj, e):
        if obj is self.list.viewport() and e.type() == QEvent.Resize:
            QTimer.singleShot(0, self._fit_rows)
        return super().eventFilter(obj, e)

    def show_list(self):
        self.stack.setCurrentIndex(0)
        self.search.setFocus()

    def _mail_toggled(self, on):
        self.settings.setValue("client_data/mail_full", on)
        if self.client_id:
            self.open_client(self.client_id)

    def open_client(self, client_id):
        c = self.state.db.client(client_id)
        if not c:
            return
        self.client_id = client_id
        self.name_label.setText(c.full_name or "(без имени)")
        clear_layout(self.stage_host)
        self.stage_host.addWidget(stage_pill(c.stage))
        name = " ".join((c.full_name or "").split())
        email = (c.email or "").strip()
        login, _, domain = email.partition("@")
        full = self.settings.value("client_data/mail_full", False, type=bool)
        mail_full = QCheckBox("@" + (domain or "gmail.com"))  # e-mail целиком или без «@домен»
        mail_full.setStyleSheet("font-size:12px;")
        mail_full.setChecked(full)
        mail_full.toggled.connect(self._mail_toggled)
        phone_a, phone_b = split_phone(c.phone)
        arc_a, arc_b = split_arc(c.arc_number)
        clear_layout(self.fields_lay)
        self.fields = [
            CopyField(1, "ФИО — заглавными", name.upper()),
            CopyField(2, "ФИО — строчными", name.lower()),
            CopyField(3, "E-mail", email if full else login),
            CopyField(4, "Тел. 010-", phone_a, mono=True, center=True, compact=True),
            CopyField(5, "Тел. конец", phone_b, mono=True, center=True, compact=True),
            CopyField(6, "ID · рожд.", arc_a, mono=True, center=True, compact=True),
            CopyField(7, "ID · 7 цифр", arc_b, mono=True, center=True, compact=True),
            CopyField(8, "Адрес", " ".join((c.registered_address or "").split())),
        ]
        f = self.fields
        self.fields_lay.addLayout(vbox(f[0], f[1], spacing=7))
        mail_row = vbox(f[2], spacing=2)
        mail_row.addWidget(mail_full, 0, Qt.AlignRight)
        self.fields_lay.addLayout(mail_row)
        self.fields_lay.addLayout(hbox(f[3], f[4], spacing=7))
        self.fields_lay.addLayout(hbox(f[5], f[6], spacing=7))
        self.fields_lay.addWidget(f[7])
        self.stack.setCurrentIndex(1)
        self.setFocus()
