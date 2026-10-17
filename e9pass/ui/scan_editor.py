"""Редактор скана: 4 угла (perspective crop), фильтр, предпросмотр листа А4."""

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QButtonGroup, QDialog, QGraphicsEllipseItem, QGraphicsItem,
                               QGraphicsPixmapItem, QGraphicsPolygonItem, QGraphicsScene, QGraphicsView,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QSlider)

from .. import scan, services
from ..db import BUILTIN_FRONT
from ..ocr.ocr_engine import find_card_corners, load_image
from . import theme
from .widgets import button, frame, hbox, label, pil_to_pixmap, vbox, wrap_label

WORK_SIZE = 1600


class Handle(QGraphicsEllipseItem):
    """Точка угла: размер на экране постоянный при любом увеличении."""

    def __init__(self, on_move, radius=9):
        super().__init__(-radius, -radius, 2 * radius, 2 * radius)
        self.on_move = on_move
        self.setBrush(QBrush(QColor(theme.ACCENT)))
        self.setPen(QPen(QColor("#121212"), 3))
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemSendsGeometryChanges
                      | QGraphicsItem.ItemIgnoresTransformations)
        self.setCursor(Qt.SizeAllCursor)
        self.setZValue(10)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.on_move()
        return super().itemChange(change, value)


class CropView(QGraphicsView):
    """Фото с 4 точками. Колесико — увеличить/уменьшить под курсором, перетаскивание фона — сдвиг,
    двойной щелчок — вписать целиком."""

    MAX_ZOOM = 12.0

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.zoom = 1.0
        self.pix_item = None
        self.poly = None
        self.handles = []
        self.changed = lambda: None

    def set_image(self, pil_img, corners):
        scene = self.scene()
        scene.clear()
        self.pix_item = QGraphicsPixmapItem(pil_to_pixmap(pil_img))
        scene.addItem(self.pix_item)
        scene.setSceneRect(QRectF(0, 0, pil_img.width, pil_img.height))
        self.poly = QGraphicsPolygonItem()
        pen = QPen(QColor(theme.ACCENT), 2)
        pen.setCosmetic(True)  # толщина линии не растет при увеличении
        self.poly.setPen(pen)
        self.poly.setBrush(QBrush(QColor(245, 197, 24, 30)))
        scene.addItem(self.poly)
        self.handles = [Handle(self._moved) for _ in range(4)]
        for h, (x, y) in zip(self.handles, corners):
            scene.addItem(h)
            h.setPos(x, y)
        self._moved()
        self.fit()

    def fit(self):
        self.zoom = 1.0
        self.fitInView(self.scene().sceneRect(), Qt.KeepAspectRatio)

    def wheelEvent(self, e):
        if not self.pix_item:
            return
        step = 1.25 if e.angleDelta().y() > 0 else 1 / 1.25
        new = min(self.MAX_ZOOM, max(1.0, self.zoom * step))
        if new == 1.0:
            self.fit()
        elif new != self.zoom:
            self.scale(new / self.zoom, new / self.zoom)
            self.zoom = new
        e.accept()

    def mouseDoubleClickEvent(self, e):
        if self.itemAt(e.position().toPoint()) in self.handles:
            return super().mouseDoubleClickEvent(e)
        self.fit()

    def corners(self):
        return [(h.pos().x(), h.pos().y()) for h in self.handles]

    def _moved(self):
        if self.poly is not None and len(self.handles) == 4:
            self.poly.setPolygon(QPolygonF([h.pos() for h in self.handles]))
            self.changed()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.pix_item and self.zoom == 1.0:
            self.fitInView(self.scene().sceneRect(), Qt.KeepAspectRatio)


class ScanEditor(QDialog):
    def __init__(self, state, client_id, doc, parent=None):
        super().__init__(parent)
        self.state, self.client_id, self.doc = state, client_id, doc
        self.is_front = doc.doc_type == BUILTIN_FRONT
        self.setWindowTitle("Редактор скана")
        self.resize(1300, 880)
        self.rotation = 0
        self.result_path = None
        self.original = load_image(doc.path)

        client = state.db.client(client_id)
        title = label("Редактор скана", h2=True)
        sub = label(f"{doc.path.name} · {'ID лицо' if self.is_front else 'ID оборот'}", muted=True)

        # ФИО и ARC крупно — сверить с картой и сразу исправить ошибку OCR
        self.client = client
        self.name_edit = QLineEdit(client.full_name)
        self.name_edit.setPlaceholderText("ФИО латиницей, как на карте")
        self.name_edit.setStyleSheet("font-size:22px;font-weight:700;letter-spacing:1px;padding:6px 10px;")
        self.arc_edit = QLineEdit(client.arc_number)
        self.arc_edit.setPlaceholderText("Номер ARC")
        self.arc_edit.setFixedWidth(230)
        self.arc_edit.setStyleSheet("font-size:20px;font-family:Consolas,monospace;padding:6px 10px;")
        check = vbox(label("ПРОВЕРЬТЕ ПО КАРТЕ: ФИО И НОМЕР (OCR, МОЖНО ИСПРАВИТЬ)", caps=True),
                     hbox(self.name_edit, self.arc_edit, spacing=10), spacing=6)

        self.view = CropView()
        self.view.changed = self._schedule_preview
        auto_b = button("Найти углы автоматически", on_click=self.auto_corners)
        rot_b = button("Повернуть 90°", on_click=self.rotate)
        reset_b = button("Сбросить", on_click=self.reset_corners)
        left = vbox(label("Потяните желтые точки к углам карты · колесико мыши — увеличить, "
                          "перетащить фон — сдвинуть, двойной щелчок — целиком", muted=True), self.view,
                    hbox(auto_b, rot_b, reset_b, None), spacing=10)

        # фильтр
        self.filter_group = QButtonGroup(self)
        seg = hbox(spacing=0)
        for i, (mode, text) in enumerate(((scan.FILTER_ORIGINAL, "Оригинал"), (scan.FILTER_CONTRAST, "Контраст"),
                                          (scan.FILTER_CLEAN, "Чистый скан"))):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setProperty("chip", True)
            b.setProperty("mode", mode)
            b.setStyleSheet("border-radius:0;" if i == 1 else
                            ("border-top-right-radius:0;border-bottom-right-radius:0;" if i == 0 else
                             "border-top-left-radius:0;border-bottom-left-radius:0;"))
            self.filter_group.addButton(b, i)
            seg.addWidget(b)
        self.filter_group.button(1).setChecked(True)
        self.filter_group.buttonClicked.connect(lambda _b: self._schedule_preview())
        self.strength = QSlider(Qt.Horizontal)
        self.strength.setRange(0, 100)
        self.strength.setValue(50)
        self.strength.valueChanged.connect(self._schedule_preview)
        self.margin = QSlider(Qt.Horizontal)
        self.margin.setRange(0, 30)
        self.margin.setValue(10)
        self.margin_label = label("Поля слева и справа: 10 мм", dim=True)
        self.margin.valueChanged.connect(lambda v: (self.margin_label.setText(f"Поля слева и справа: {v} мм"),
                                                    self._schedule_preview()))
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setFixedSize(220, 311)
        self.preview.setStyleSheet("background:#FFFFFF;border-radius:3px;")

        right = vbox(label("1 · ФИЛЬТР СКАНА", caps=True), seg,
                     label("Сила фильтра", dim=True), self.strength, spacing=10)
        if self.is_front:
            right.addSpacing(10)
            right.addWidget(label("2 · ЛИСТ А4 (ТОЛЬКО ЛИЦО)", caps=True))
            right.addLayout(hbox(self.preview, None))
            right.addWidget(self.margin_label)
            right.addWidget(self.margin)
            right.addWidget(wrap_label("Карта растянута на всю ширину листа и стоит по центру.", dim=True))
        else:
            right.addSpacing(10)
            right.addWidget(label("ПРЕДПРОСМОТР", caps=True))
            self.preview.setFixedSize(300, 190)
            self.preview.setStyleSheet("background:#0E0E0E;border-radius:6px;")
            right.addLayout(hbox(self.preview, None))
        right.addStretch(1)
        right.addLayout(hbox(button("Отмена", on_click=self.reject),
                             button("Применить и сохранить", primary=True, on_click=self.apply)))
        right.addWidget(wrap_label("Оригинал не изменяется — создается обработанная копия", dim=True))
        rw = frame("panel")
        rw.setLayout(right)
        right.setContentsMargins(18, 16, 18, 16)
        rw.setFixedWidth(380)

        self.setLayout(vbox(hbox(title, sub, None, spacing=14), check, hbox(left, rw, spacing=16),
                            spacing=12, margins=(20, 16, 20, 16)))
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.update_preview)
        self._load_work_image()

    # ------------------------------------------------------------ изображение

    def _load_work_image(self, corners=None):
        img = self.original.rotate(self.rotation, expand=True) if self.rotation else self.original
        self.scale = min(1.0, WORK_SIZE / max(img.size))
        self.work = img.resize((round(img.width * self.scale), round(img.height * self.scale))) \
            if self.scale < 1 else img
        if corners is None:
            pts = find_card_corners(self.work)
            corners = pts.tolist() if pts is not None else self._default_corners()
        self.view.set_image(self.work, corners)
        self.update_preview()

    def _default_corners(self):
        w, h = self.work.size
        mx, my = w * 0.08, h * 0.08
        return [(mx, my), (w - mx, my), (w - mx, h - my), (mx, h - my)]

    def auto_corners(self):
        pts = find_card_corners(self.work)
        if pts is None:
            QMessageBox.information(self, "Углы", "Карту не удалось найти автоматически — поставьте углы вручную.")
            return
        self._load_work_image(pts.tolist())

    def reset_corners(self):
        self._load_work_image(self._default_corners())

    def rotate(self):
        self.rotation = (self.rotation + 90) % 360
        self._load_work_image()

    def _filter(self):
        return self.filter_group.checkedButton().property("mode")

    def _schedule_preview(self):
        self.timer.start()

    def update_preview(self):
        if not self.view.handles:
            return
        card = scan.warp_perspective(self.work, self.view.corners())
        card = scan.apply_filter(card, self._filter(), self.strength.value())
        if self.is_front:
            page = scan.a4_page(card, self.margin.value())
            page.thumbnail((220, 311))
            self.preview.setPixmap(pil_to_pixmap(page))
        else:
            card.thumbnail((300, 190))
            self.preview.setPixmap(pil_to_pixmap(card))

    def save_profile(self) -> bool:
        """Записывает исправленные ФИО и ARC. True — если что-то изменилось."""
        name, arc = self.name_edit.text().strip(), self.arc_edit.text().strip()
        changes = {}
        if name and name != self.client.full_name:
            changes["full_name"] = name
        if arc and arc != self.client.arc_number:
            changes["arc_number"] = arc
        if changes:
            services.set_client_fields(self.state.db, self.client_id, **changes)
        return bool(changes)

    def done(self, result):
        # исправленное ФИО сохраняется и при «Отмена» — отменяется только обработка скана
        if self.save_profile() and result != QDialog.Accepted:
            self.state.data_changed.emit()
        super().done(result)

    def apply(self):
        corners = [(x / self.scale, y / self.scale) for x, y in self.view.corners()]
        db = self.state.db
        if self.is_front:
            self.result_path = services.make_id_a4(db, self.client_id, self.doc.path, corners, self._filter(),
                                                   self.strength.value(), self.margin.value(), rotate=self.rotation,
                                                   manual=True)
        else:
            self.result_path = services.save_back_scan(db, self.client_id, self.doc.path, corners, self._filter(),
                                                       self.strength.value(), rotate=self.rotation)
        db.update_client(self.client_id, master_pdf="")  # Master-PDF нужно пересобрать
        self.accept()
