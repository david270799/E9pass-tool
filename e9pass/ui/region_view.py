"""Фото карты с рамками полей (ФИО, номер, страна, адрес): рамку можно подвинуть за углы."""

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QBrush, QColor, QFont, QPen
from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsSimpleTextItem

from .preview import ZoomView
from .scan_editor import Handle

FIELD_STYLE = {"name": ("ФИО", "#F5C518"), "arc": ("Номер", "#4DA3FF"), "country": ("Страна", "#3DDC84"),
               "address": ("Адрес", "#F5C518")}


class RegionBox:
    """Прямоугольник поля + 4 угловые точки. Тянешь угол — противоположный остается на месте."""

    def __init__(self, scene, field: str, rect: QRectF):
        title, color = FIELD_STYLE.get(field, (field, "#FFFFFF"))
        self.field = field
        pen = QPen(QColor(color), 2)
        pen.setCosmetic(True)
        self.rect_item = QGraphicsRectItem(rect)
        self.rect_item.setPen(pen)
        c = QColor(color)
        c.setAlpha(35)
        self.rect_item.setBrush(QBrush(c))
        self.rect_item.setZValue(5)
        scene.addItem(self.rect_item)
        self.label = QGraphicsSimpleTextItem(title)
        self.label.setBrush(QBrush(QColor(color)))
        f = QFont()
        f.setBold(True)
        f.setPointSize(11)
        self.label.setFont(f)
        self.label.setFlag(QGraphicsSimpleTextItem.ItemIgnoresTransformations)
        self.label.setZValue(6)
        scene.addItem(self.label)
        self._moving = False
        self.handles = []
        for i in range(4):
            h = Handle(lambda i=i: self._moved(i), radius=5)
            h.setBrush(QBrush(QColor(color)))
            scene.addItem(h)
            self.handles.append(h)
        self._place(rect)

    def _place(self, rect: QRectF):
        self._moving = True
        pts = [rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft()]
        for h, p in zip(self.handles, pts):
            h.setPos(p)
        self.rect_item.setRect(rect)
        self.label.setPos(rect.topLeft() + QPointF(0, -2))
        self._moving = False

    def _moved(self, i: int):
        if self._moving:
            return
        p = self.handles[i].pos()
        opposite = self.handles[(i + 2) % 4].pos()
        self._place(QRectF(p, opposite).normalized())

    def rect(self) -> QRectF:
        return self.rect_item.rect()

    def remove(self, scene):
        for it in [self.rect_item, self.label, *self.handles]:
            scene.removeItem(it)


class RegionView(ZoomView):
    def __init__(self):
        super().__init__()
        self.boxes = {}

    def set_pixmap(self, pm):
        self.boxes = {}
        super().set_pixmap(pm)

    def set_regions(self, regions: dict):
        for b in self.boxes.values():
            b.remove(self.scene())
        self.boxes = {}
        if not self.item:
            return
        w, h = self.item.pixmap().width(), self.item.pixmap().height()
        for field, (x0, y0, x1, y1) in regions.items():
            self.boxes[field] = RegionBox(self.scene(), field, QRectF(x0 * w, y0 * h, (x1 - x0) * w, (y1 - y0) * h))

    def regions(self) -> dict:
        if not self.item:
            return {}
        w, h = self.item.pixmap().width(), self.item.pixmap().height()
        out = {}
        for field, b in self.boxes.items():
            r = b.rect()
            out[field] = [round(max(0.0, r.left() / w), 4), round(max(0.0, r.top() / h), 4),
                          round(min(1.0, r.right() / w), 4), round(min(1.0, r.bottom() / h), 4)]
        return out

    def mark(self, field: str):
        """Поставить (заново) рамку поля в середине видимой части фото — дальше ее тянут за углы."""
        if not self.item:
            return
        if field in self.boxes:
            self.boxes.pop(field).remove(self.scene())
        visible = self.mapToScene(self.viewport().rect()).boundingRect().intersected(self.scene().sceneRect())
        w, h = visible.width() * 0.5, visible.height() * 0.12
        rect = QRectF(visible.center().x() - w / 2, visible.center().y() - h / 2, w, h)
        self.boxes[field] = RegionBox(self.scene(), field, rect)

    def mouseDoubleClickEvent(self, e):
        # двойной щелчок по точке не должен сбрасывать масштаб
        if isinstance(self.itemAt(e.position().toPoint()), Handle):
            return
        super().mouseDoubleClickEvent(e)
