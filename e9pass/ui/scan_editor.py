"""Редактор скана: 4 угла (perspective crop), фильтр, предпросмотр листа А4."""

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QButtonGroup, QDialog, QGraphicsEllipseItem, QGraphicsItem,
                               QGraphicsPixmapItem, QGraphicsPolygonItem, QGraphicsScene, QGraphicsView,
                               QLabel, QMessageBox, QPushButton, QSlider)

from .. import scan, services
from ..db import BUILTIN_FRONT
from ..ocr.ocr_engine import find_card_corners, load_image
from . import theme
from .widgets import button, frame, hbox, label, pil_to_pixmap, vbox, wrap_label

WORK_SIZE = 1600


class Handle(QGraphicsEllipseItem):
    def __init__(self, on_move, radius=11):
        super().__init__(-radius, -radius, 2 * radius, 2 * radius)
        self.on_move = on_move
        self.setBrush(QBrush(QColor(theme.ACCENT)))
        self.setPen(QPen(QColor("#121212"), 3))
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemSendsGeometryChanges)
        self.setCursor(Qt.SizeAllCursor)
        self.setZValue(10)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.on_move()
        return super().itemChange(change, value)


class CropView(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
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
        self.poly.setPen(QPen(QColor(theme.ACCENT), 3))
        self.poly.setBrush(QBrush(QColor(245, 197, 24, 30)))
        scene.addItem(self.poly)
        radius = max(10, pil_img.width // 90)
        self.handles = [Handle(self._moved, radius) for _ in range(4)]
        for h, (x, y) in zip(self.handles, corners):
            scene.addItem(h)
            h.setPos(x, y)
        self._moved()
        self.fitInView(scene.sceneRect(), Qt.KeepAspectRatio)

    def corners(self):
        return [(h.pos().x(), h.pos().y()) for h in self.handles]

    def _moved(self):
        if self.poly is not None and len(self.handles) == 4:
            self.poly.setPolygon(QPolygonF([h.pos() for h in self.handles]))
            self.changed()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.pix_item:
            self.fitInView(self.scene().sceneRect(), Qt.KeepAspectRatio)


class ScanEditor(QDialog):
    def __init__(self, state, client_id, doc, parent=None):
        super().__init__(parent)
        self.state, self.client_id, self.doc = state, client_id, doc
        self.is_front = doc.doc_type == BUILTIN_FRONT
        self.setWindowTitle("Редактор скана")
        self.resize(1300, 820)
        self.rotation = 0
        self.original = load_image(doc.path)

        client = state.db.client(client_id)
        title = label("Редактор скана", h2=True)
        sub = label(f"{client.full_name} · {doc.path.name} · {'ID лицо' if self.is_front else 'ID оборот'}", muted=True)

        self.view = CropView()
        self.view.changed = self._schedule_preview
        auto_b = button("Найти углы автоматически", on_click=self.auto_corners)
        rot_b = button("Повернуть 90°", on_click=self.rotate)
        reset_b = button("Сбросить", on_click=self.reset_corners)
        left = vbox(label("Потяните желтые точки к углам карты", muted=True), self.view,
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

        self.setLayout(vbox(hbox(title, sub, None, spacing=14), hbox(left, rw, spacing=16),
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

    def apply(self):
        corners = [(x / self.scale, y / self.scale) for x, y in self.view.corners()]
        db = self.state.db
        if self.is_front:
            services.make_id_a4(db, self.client_id, self.doc.path, corners, self._filter(), self.strength.value(),
                                self.margin.value(), rotate=self.rotation)
        else:
            services.save_back_scan(db, self.client_id, self.doc.path, corners, self._filter(),
                                    self.strength.value(), rotate=self.rotation)
        db.update_client(self.client_id, master_pdf="")  # Master-PDF нужно пересобрать
        self.accept()
