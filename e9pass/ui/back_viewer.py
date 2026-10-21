"""Оборот ID: крупный просмотр фото и правка адреса (без обработки скана)."""

from PySide6.QtWidgets import QDialog, QLineEdit

from .. import services
from ..ocr.ocr_engine import load_image
from .preview import ZoomView
from .widgets import button, hbox, label, pil_to_pixmap, vbox


class BackViewer(QDialog):
    def __init__(self, state, client_id, doc, parent=None):
        super().__init__(parent)
        self.state, self.client_id, self.doc = state, client_id, doc
        self.client = state.db.client(client_id)
        self.setWindowTitle("Оборот ID")
        self.resize(1100, 860)
        self.rotation = 0
        self.changed = False

        self.addr_edit = QLineEdit(self.client.registered_address)
        self.addr_edit.setPlaceholderText("Адрес регистрации (체류지), как на обороте карты — последний")
        self.addr_edit.setStyleSheet("font-size:19px;font-weight:600;padding:6px 10px;")
        self.view = ZoomView()
        self.image = load_image(doc.path)
        self.image.thumbnail((3000, 3000))

        self.setLayout(vbox(
            hbox(label("Оборот ID", h2=True), label(doc.original_name or doc.path.name, muted=True), None, spacing=14),
            label("ПРОВЕРЬТЕ ПО КАРТЕ: АДРЕС (OCR, МОЖНО ИСПРАВИТЬ)", caps=True),
            self.addr_edit,
            label("Колесико мыши — увеличить, перетащить — сдвинуть, двойной щелчок — целиком", muted=True),
            self.view,
            hbox(button("Повернуть 90°", on_click=self.rotate), None,
                 button("Закрыть", on_click=self.reject),
                 button("Сохранить адрес", primary=True, on_click=self.accept)),
            spacing=10, margins=(20, 16, 20, 16)))
        self._show_image()

    def _show_image(self):
        img = self.image.rotate(self.rotation, expand=True) if self.rotation else self.image
        self.view.set_pixmap(pil_to_pixmap(img))

    def rotate(self):
        self.rotation = (self.rotation - 90) % 360  # по часовой стрелке
        self._show_image()

    def showEvent(self, e):
        super().showEvent(e)
        self.view.fit()

    def save_address(self) -> bool:
        addr = " ".join(self.addr_edit.text().split())
        if addr and addr != self.client.registered_address:
            services.set_client_fields(self.state.db, self.client_id, registered_address=addr)
            return True
        return False

    def done(self, result):
        # исправленный адрес сохраняется и при «Закрыть»
        self.changed = self.save_address()
        super().done(result)
