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
        brand = vbox(label("E9pay"), label("документы клиентов", dim=True), spacing=0)
        brand.itemAt(0).widget().setStyleSheet("font-weight:800;font-size:18px;")
        self.nav = QButtonGroup(self)
        self.nav_buttons = {}
        items = [("dashboard", "1   Импорт и реестр"), ("intake", "      Приемка архивов"),
                 ("client", "2   Документы клиента"), ("print", "3   Печать отчетности"),
                 ("doctypes", "☰   Типы документов")]
        nav_lay = vbox(spacing=4)
        for key, text in items:
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
        side.setLayout(vbox(hbox(logo, brand, None, spacing=10, margins=(6, 0, 0, 18)), nav_lay, None,
                            self.busy, self.folder_label, spacing=6, margins=(16, 22, 16, 16)))

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


def main():
    import logging
    logging.basicConfig(filename=str(config.data_dir() / "e9pay.log"), level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("e9pay").info("запуск E9pay")
    app = QApplication(sys.argv)
    app.setApplicationName("E9pay")
    app.setStyleSheet(theme.QSS)
    win = MainWindow(Database())
    win.show()
    sys.exit(app.exec())
