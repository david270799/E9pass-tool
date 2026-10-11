"""Окно просмотра документа (PDF или фото) внутри программы."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QDialog, QLabel, QScrollArea

from .. import pdf_tools
from .widgets import button, hbox, label, open_path, pil_to_pixmap, vbox


class PreviewDialog(QDialog):
    def __init__(self, path, title: str = "", parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.setWindowTitle(f"Просмотр — {title or self.path.name}")
        self.resize(900, 1000)
        self.page = 0
        self.zoom_fit = True
        self.is_pdf = pdf_tools.real_extension(self.path) == ".pdf"
        self.pages = pdf_tools.page_count(self.path) if self.is_pdf else 1

        self.view = QLabel()
        self.view.setAlignment(Qt.AlignCenter)
        self.view.setStyleSheet("background:#0E0E0E;")
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.view)
        self.info = label("", muted=True)
        self.prev_b = button("‹ Назад", small=True, on_click=lambda: self.go(-1))
        self.next_b = button("Вперед ›", small=True, on_click=lambda: self.go(1))
        self.zoom_b = button("100%", small=True, on_click=self.toggle_zoom)
        ext_b = button("Открыть в программе Windows", small=True, on_click=lambda: open_path(self.path))
        close_b = button("Закрыть", primary=True, on_click=self.accept)
        name = label(title or self.path.name, h2=True)
        self.setLayout(vbox(hbox(name, None, self.info, spacing=12), self.scroll,
                            hbox(self.prev_b, self.next_b, self.zoom_b, None, ext_b, close_b),
                            spacing=10, margins=(16, 14, 16, 14)))
        self.render()

    def _pixmap(self, max_w: int, max_h: int) -> QPixmap:
        if self.is_pdf:
            import pymupdf
            with pymupdf.open(self.path) as doc:
                if doc.needs_pass:
                    return QPixmap()
                page = doc[self.page]
                if self.zoom_fit:
                    zoom = min(max_w / page.rect.width, max_h / page.rect.height)
                else:
                    zoom = 150 / 72
                pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
                return QPixmap.fromImage(img.copy())
        from ..ocr.ocr_engine import load_image
        img = load_image(self.path)
        if self.zoom_fit:
            img.thumbnail((max_w, max_h))
        return pil_to_pixmap(img)

    def render(self):
        vp = self.scroll.viewport().size()
        pm = self._pixmap(max(vp.width() - 8, 200), max(vp.height() - 8, 200))
        if pm.isNull():
            self.view.setText("Файл защищен паролем — сначала снимите пароль в карточке клиента")
        else:
            self.view.setPixmap(pm)
        self.info.setText(f"стр. {self.page + 1} из {self.pages}" if self.is_pdf else "фото")
        self.prev_b.setEnabled(self.page > 0)
        self.next_b.setEnabled(self.page < self.pages - 1)
        self.zoom_b.setText("По размеру окна" if not self.zoom_fit else "100%")

    def go(self, delta):
        self.page = max(0, min(self.pages - 1, self.page + delta))
        self.render()

    def toggle_zoom(self):
        self.zoom_fit = not self.zoom_fit
        self.render()

    def showEvent(self, e):
        super().showEvent(e)
        self.render()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.zoom_fit:
            self.render()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Right, Qt.Key_PageDown):
            self.go(1)
        elif e.key() in (Qt.Key_Left, Qt.Key_PageUp):
            self.go(-1)
        else:
            super().keyPressEvent(e)


def show_preview(path, title: str = "", parent=None):
    PreviewDialog(path, title, parent).exec()
