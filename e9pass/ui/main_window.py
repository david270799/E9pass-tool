"""Главное окно: боковое меню (шаги 1–3, типы документов) и экраны."""

import sys

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication, QButtonGroup, QFrame, QLabel, QMainWindow, QStackedWidget, QWidget

from .. import config
from ..db import Database
from . import theme
from .client import ClientPage
from .dashboard import DashboardPage
from .doctypes import DocTypesPage
from .intake import IntakePage
from .printing import PrintPage
from .state import AppState
from .widgets import FullDropOverlay, button, hbox, label, vbox, wrap_label


def rounded_logo(size: int) -> QPixmap:
    src = QPixmap(str(config.assets_dir() / "logo.jpg"))
    if src.isNull():
        return QPixmap()
    src = src.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, size, size, size * 0.22, size * 0.22)
    p.setClipPath(path)
    p.drawPixmap(0, 0, src)
    p.end()
    return out


class MainWindow(QMainWindow):
    def __init__(self, db: Database):
        super().__init__()
        self.setWindowTitle("E9pay — документы клиентов")
        self.setWindowIcon(QIcon(str(config.assets_dir() / "logo.jpg")))
        self.resize(1440, 900)
        self.state = AppState(db)

        # --- боковое меню
        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(248)
        logo = QLabel()
        logo.setPixmap(rounded_logo(44))
        brand_w = QWidget()
        brand = vbox(label("E9pay"), label("документы клиентов", dim=True), spacing=0)
        brand.itemAt(0).widget().setStyleSheet("font-weight:800;font-size:18px;")
        brand_w.setLayout(brand)
        self.nav = QButtonGroup(self)
        self.nav_buttons = {}
        # (ключ, текст, короткая подпись для свернутого меню)
        items = [("dashboard", "1   Импорт и реестр", "1"), ("intake", "      Приемка архивов", "⤓"),
                 ("client", "2   Документы клиента", "2"), ("print", "3   Печать отчетности", "3"),
                 ("doctypes", "☰   Типы документов", "☰")]
        self.nav_texts = {}
        nav_lay = vbox(spacing=4)
        for key, text, short in items:
            self.nav_texts[key] = (text, short)
            b = button(text)
            b.setObjectName("NavButton")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=key: self.show_page(k))
            self.nav.addButton(b)
            self.nav_buttons[key] = b
            nav_lay.addWidget(b)
            if key == "print":
                line = QFrame()
                line.setFixedHeight(1)
                line.setStyleSheet("background:rgba(255,255,255,0.10);margin:10px 6px;")
                nav_lay.addWidget(line)
        self.busy = wrap_label("", dim=True)
        self.folder_label = wrap_label(f"Офлайн-режим\n{config.data_dir()}", dim=True)
        self.ext_btn = button("🧩  Расширение Chrome", flat=True, on_click=self.show_extension)
        self.ext_btn.setStyleSheet(f"QPushButton{{color:{theme.MUTED};text-align:left;}}"
                                   f"QPushButton:hover{{color:{theme.ACCENT};}}")
        self.collapse_btn = button("«  Свернуть меню", flat=True, on_click=self.toggle_sidebar)
        self.collapse_btn.setStyleSheet(f"QPushButton{{color:{theme.DIM};text-align:left;}}"
                                        f"QPushButton:hover{{color:{theme.ACCENT};}}")
        self.side, self.brand_w = side, brand_w
        self.side_lay = vbox(hbox(logo, brand_w, None, spacing=10, margins=(6, 0, 0, 18)), nav_lay, None,
                             self.busy, self.folder_label, self.ext_btn, self.collapse_btn, spacing=6,
                             margins=(16, 22, 16, 16))
        side.setLayout(self.side_lay)

        # --- экраны
        self.stack = QStackedWidget()
        self.pages = {
            "dashboard": DashboardPage(self.state),
            "client": ClientPage(self.state),
            "doctypes": DocTypesPage(self.state),
            "print": PrintPage(self.state),
        }
        self.pages["intake"] = IntakePage(self.state, on_status=self.pages["dashboard"].set_import_status)
        for p in self.pages.values():
            self.stack.addWidget(p)

        root = QWidget()
        root.setLayout(hbox(side, self.stack, spacing=0))
        self.setCentralWidget(root)
        # Перетаскивание файлов в любое место окна
        self.drop_overlay = FullDropOverlay(root, self._drop_target)
        QApplication.instance().installEventFilter(self)

        self.state.navigate.connect(self.show_page)
        self.state.open_client.connect(self.open_client)
        self.state.busy_changed.connect(self.busy.setText)
        self.state.archives_dropped.connect(lambda _files: self.show_page("intake"))
        self.show_page("dashboard")
        from PySide6.QtCore import QSettings, QTimer
        self.settings = QSettings("E9pay", "E9pay")
        self.calendar_timer = QTimer(self)  # раз в минуту: не наступил ли новый месяц
        self.calendar_timer.timeout.connect(self.state.follow_calendar)
        self.calendar_timer.start(60_000)
        self.set_sidebar_collapsed(self.settings.value("sidebar_collapsed", False, type=bool))

    def show_extension(self):
        from .extension_dialog import ExtensionDialog
        ExtensionDialog(getattr(self, "bridge_port", None), self).exec()

    def toggle_sidebar(self):
        self.set_sidebar_collapsed(not self.sidebar_collapsed)
        self.settings.setValue("sidebar_collapsed", self.sidebar_collapsed)

    def set_sidebar_collapsed(self, collapsed: bool):
        """Свернутое меню: узкая полоса с логотипом и короткими подписями (полные — в подсказке)."""
        self.sidebar_collapsed = collapsed
        self.side.setFixedWidth(76 if collapsed else 248)
        self.brand_w.setVisible(not collapsed)
        self.folder_label.setVisible(not collapsed)
        self.busy.setVisible(not collapsed)
        for key, b in self.nav_buttons.items():
            text, short = self.nav_texts[key]
            b.setText(short if collapsed else text)
            b.setToolTip(text.strip() if collapsed else "")
            b.setStyleSheet("text-align:center;padding:10px 0;" if collapsed else "")
        self.collapse_btn.setText("»" if collapsed else "«  Свернуть меню")
        self.ext_btn.setText("🧩" if collapsed else "🧩  Расширение Chrome")
        self.ext_btn.setToolTip("Расширение Chrome" if collapsed else "")
        self.collapse_btn.setToolTip("Развернуть меню" if collapsed else "")
        self.side_lay.setContentsMargins(*((10, 22, 10, 16) if collapsed else (16, 22, 16, 16)))

    def _drop_target(self):
        """Что принимает текущий экран при перетаскивании: (заголовок, фильтр, обработчик)."""
        from ..archive import ARCHIVE_EXTENSIONS
        from .. import pdf_tools
        key = self._current_key()
        period = self.state.period
        if key in ("dashboard", "intake"):
            return (f"Отпустите, чтобы загрузить архивы в {period}",
                    lambda f: f.suffix.lower() in ARCHIVE_EXTENSIONS | {".e9pay"},
                    self._dropped_on_dashboard)
        if key == "client" and self.pages["client"].client_id:
            page = self.pages["client"]
            c = page.client()
            if c is None or not page.drop.isEnabled():
                return None
            return (f"Отпустите, чтобы добавить документы: {c.full_name or 'клиент'}",
                    lambda f: pdf_tools.real_extension(f) in pdf_tools.KNOWN_EXTENSIONS,
                    page.add_files)
        return None

    def _dropped_on_dashboard(self, files):
        """Архивы — на приемку, файлы .e9pay — прием клиентов от коллеги."""
        from .transfer_dialogs import import_package_flow
        archives = [f for f in files if f.suffix.lower() != ".e9pay"]
        if archives:
            self.state.archives_dropped.emit(archives)
        for f in files:
            if f.suffix.lower() == ".e9pay":
                import_package_flow(self, self.state, f)

    def eventFilter(self, obj, event):
        # Начало перетаскивания над любым виджетом окна — показываем подсветку на всё окно.
        if event.type() == QEvent.DragEnter and not self.drop_overlay.isVisible():
            if isinstance(obj, QWidget) and obj.window() is self and self.drop_overlay.begin(event.mimeData()):
                event.ignore()
                return True
        return super().eventFilter(obj, event)

    def show_page(self, key: str):
        if key == "client" and not self.pages["client"].client_id:
            first = self.state.db.clients(self.state.period)
            if not first:
                self.nav_buttons[self._current_key()].setChecked(True)
                self.busy.setText("Сначала выберите клиента на дашборде")
                return
            self.pages["client"].open(first[0].id)
        page = self.pages[key]
        if hasattr(page, "refresh") and key in ("client", "print", "doctypes", "dashboard"):
            page.refresh()
        self.stack.setCurrentWidget(page)
        self.nav_buttons[key].setChecked(True)

    def _current_key(self):
        cur = self.stack.currentWidget()
        return next((k for k, p in self.pages.items() if p is cur), "dashboard")

    def open_client(self, client_id: int):
        self.pages["client"].open(client_id)
        self.show_page("client")


INSTANCE_KEY = "E9pay-single-instance"


def single_instance(app):
    """Одна копия программы. Если E9pay уже запущена — просим ее показать окно и возвращаем None.
    Иначе возвращаем сервер, который будет принимать такие просьбы от следующих запусков."""
    import getpass

    from PySide6.QtNetwork import QLocalServer, QLocalSocket

    key = f"{INSTANCE_KEY}-{getpass.getuser()}"
    sock = QLocalSocket()
    sock.connectToServer(key)
    if sock.waitForConnected(500):
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(500)
        sock.disconnectFromServer()
        return None
    QLocalServer.removeServer(key)  # остаток после аварийного завершения
    server = QLocalServer(app)
    server.listen(key)
    return server


def bring_to_front(win):
    """Показать окно поверх остальных (в том числе свернутое)."""
    if win.isMinimized():
        win.showNormal()
    win.show()
    win.raise_()
    win.activateWindow()
    QApplication.alert(win)  # если Windows не дает вывести окно вперед — мигает значок на панели задач


def main():
    import logging
    logging.basicConfig(filename=str(config.data_dir() / "e9pay.log"), level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("e9pay").info("запуск E9pay")
    if sys.platform == "win32":
        try:  # свой значок на панели задач: клик сворачивает / разворачивает именно это окно
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("E9pay.App")
        except Exception:  # noqa: BLE001
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("E9pay")
    server = single_instance(app)
    if server is None:
        logging.getLogger("e9pay").info("E9pay уже открыта — показываю ее окно")
        return 0
    app.setStyleSheet(theme.QSS)
    db = Database()
    win = MainWindow(db)
    win.setWindowFlags(Qt.Window | Qt.WindowTitleHint | Qt.WindowSystemMenuHint
                       | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)

    def on_second_launch():
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.readAll()
            conn.disconnectFromServer()
        bring_to_front(win)

    server.newConnection.connect(on_second_launch)
    win.show()
    # связь с расширением Chrome (только 127.0.0.1, только наше расширение)
    from .. import browser_bridge
    from .extension_dialog import temp_number
    win.bridge_port = browser_bridge.start(db, get_period=lambda: win.state.period, get_temp=temp_number)
    sys.exit(app.exec())
