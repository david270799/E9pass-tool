"""Окно просмотра документа (PDF или фото) внутри программы."""

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import QDialog, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView

from .. import pdf_tools
from .widgets import button, hbox, label, open_path, pil_to_pixmap, vbox


class ZoomView(QGraphicsView):
    """Картинка во весь размер окна. Колесико — увеличить/уменьшить под курсором,
    перетаскивание — сдвиг, двойной щелчок — целиком."""

    MAX_ZOOM = 8.0

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setStyleSheet("background:#0E0E0E;border:none;")
        self.item = None
        self.zoom = 1.0

    def set_pixmap(self, pm: QPixmap):
        self.scene().clear()
        self.item = QGraphicsPixmapItem(pm)
        self.item.setTransformationMode(Qt.SmoothTransformation)
        self.scene().addItem(self.item)
        self.scene().setSceneRect(QRectF(pm.rect()))
        self.fit()

    def set_text(self, text: str):
        self.scene().clear()
        self.item = None
        self.scene().addText(text).setDefaultTextColor(Qt.white)

    def fit(self):
        self.zoom = 1.0
        self.resetTransform()
        if self.item:
            self.fitInView(self.scene().sceneRect(), Qt.KeepAspectRatio)

    def wheelEvent(self, e):
        if not self.item:
            return
        step = 1.25 if e.angleDelta().y() > 0 else 1 / 1.25
        new = min(self.MAX_ZOOM, max(1.0, self.zoom * step))
        if new == 1.0:
            self.fit()
        elif new != self.zoom:
            self.scale(new / self.zoom, new / self.zoom)
            self.zoom = new

    def mouseDoubleClickEvent(self, _e):
        self.fit()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.zoom == 1.0:
            self.fit()


class PreviewDialog(QDialog):
    def __init__(self, path, title: str = "", parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.setWindowTitle(f"Просмотр — {title or self.path.name}")
        self.resize(900, 1000)
        self.page = 0
        self.is_pdf = pdf_tools.real_extension(self.path) == ".pdf"
        self.pages = pdf_tools.page_count(self.path) if self.is_pdf else 1

        self.view = ZoomView()
        self.info = label("", muted=True)
        self.prev_b = button("‹ Назад", small=True, on_click=lambda: self.go(-1))
        self.next_b = button("Вперед ›", small=True, on_click=lambda: self.go(1))
        self.zoom_b = button("Целиком", small=True, on_click=lambda: self.view.fit())
        ext_b = button("Открыть в программе Windows", small=True, on_click=lambda: open_path(self.path))
        close_b = button("Закрыть", primary=True, on_click=self.accept)
        name = label(title or self.path.name, h2=True)
        hint = label("колесико мыши — увеличить, перетащить — сдвинуть, двойной щелчок — целиком", dim=True)
        self.setLayout(vbox(hbox(name, None, self.info, spacing=12), self.view, hint,
                            hbox(self.prev_b, self.next_b, self.zoom_b, None, ext_b, close_b),
                            spacing=10, margins=(16, 14, 16, 14)))
        self.render()

    def _pixmap(self) -> QPixmap:
        """Страница PDF или фото в хорошем разрешении — увеличивать можно без потери четкости."""
        if self.is_pdf:
            import pymupdf
            with pymupdf.open(self.path) as doc:
                if doc.needs_pass:
                    return QPixmap()
                page = doc[self.page]
                zoom = min(3.0, 2200 / max(page.rect.width, page.rect.height))
                pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
                return QPixmap.fromImage(img.copy())
        from ..ocr.ocr_engine import load_image
        img = load_image(self.path)
        img.thumbnail((3000, 3000))
        return pil_to_pixmap(img)

    def render(self):
        pm = self._pixmap()
        if pm.isNull():
            self.view.set_text("Файл защищен паролем — сначала снимите пароль в карточке клиента")
        else:
            self.view.set_pixmap(pm)
        self.info.setText(f"стр. {self.page + 1} из {self.pages}" if self.is_pdf else "фото")
        self.prev_b.setEnabled(self.page > 0)
        self.next_b.setEnabled(self.page < self.pages - 1)

    def go(self, delta):
        page = max(0, min(self.pages - 1, self.page + delta))
        if page != self.page:
            self.page = page
            self.render()

    def showEvent(self, e):
        super().showEvent(e)
        self.view.fit()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Right, Qt.Key_PageDown):
            self.go(1)
        elif e.key() in (Qt.Key_Left, Qt.Key_PageUp):
            self.go(-1)
        else:
            super().keyPressEvent(e)


def show_preview(path, title: str = "", parent=None):
    PreviewDialog(path, title, parent).exec()
