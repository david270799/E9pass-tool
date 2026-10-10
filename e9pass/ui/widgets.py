"""Общие виджеты и фоновые задачи."""

import traceback
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from PySide6.QtCore import QObject, QPoint, QRect, QRunnable, QSize, Qt, QThreadPool, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QImage, QPixmap
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QLayout, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

from . import theme

MONTHS_RU = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь",
             "Октябрь", "Ноябрь", "Декабрь"]

STATUS_LABELS = {"ready": "Готов", "review": "Проверить", "new": "Новый", "importing": "Импорт…"}


def label(text: str = "", **props) -> QLabel:
    w = QLabel(text)
    for k, v in props.items():
        w.setProperty(k, v)
    return w


def wrap_label(text: str = "", **props) -> QLabel:
    w = label(text, **props)
    w.setWordWrap(True)
    return w


def button(text: str, primary: bool = False, on_click: Optional[Callable] = None, **props) -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    if primary:
        b.setProperty("primary", True)
    for k, v in props.items():
        b.setProperty(k, v)
    if on_click:
        b.clicked.connect(on_click)
    return b


def frame(prop: str = "panel") -> QFrame:
    f = QFrame()
    f.setProperty(prop, True)
    return f


def hbox(*widgets, spacing: int = 10, margins=(0, 0, 0, 0)) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, QLayout):
            lay.addLayout(w)
        else:
            lay.addWidget(w)
    return lay


def vbox(*widgets, spacing: int = 10, margins=(0, 0, 0, 0)) -> QVBoxLayout:
    lay = QVBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, QLayout):
            lay.addLayout(w)
        else:
            lay.addWidget(w)
    return lay


def clear_layout(layout: QLayout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().hide()  # сразу убрать с экрана, удаление — позже
            item.widget().deleteLater()
        elif item.layout():
            clear_layout(item.layout())


class FlowLayout(QLayout):
    """Элементы в строку с переносом (для чипов)."""

    def __init__(self, spacing: int = 6):
        super().__init__()
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        return size

    def _do_layout(self, rect, test_only):
        x, y, line_h = rect.x(), rect.y(), 0
        for it in self._items:
            hint = it.sizeHint()
            if x + hint.width() > rect.right() and line_h > 0:
                x = rect.x()
                y += line_h + self._spacing
                line_h = 0
            if not test_only:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


def repolish(w: QWidget):
    w.style().unpolish(w)
    w.style().polish(w)


def badge(status: str, text: Optional[str] = None) -> QLabel:
    text = text or STATUS_LABELS.get(status, status)
    w = QLabel(text)
    if status == "ready":
        css = f"background:{theme.ACCENT};color:#121212;"
    elif status == "review":
        css = f"border:1.5px solid {theme.ACCENT};color:{theme.ACCENT};"
    else:
        css = f"border:1px solid {theme.TEXT};color:{theme.TEXT};"
    w.setStyleSheet(css + "border-radius:10px;padding:2px 10px;font-size:12px;font-weight:700;")
    w.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return w


def chip(text: str, state: str = "ok") -> QLabel:
    w = QLabel(text)
    if state == "ok":
        css = f"border:1px solid {theme.TEXT};color:{theme.TEXT};"
    elif state == "warn":
        css = f"border:1.5px solid {theme.ACCENT};color:{theme.ACCENT};"
    else:  # missing
        css = f"border:1px dashed #5C5C5C;color:{theme.DIM};"
    w.setStyleSheet(css + "border-radius:6px;padding:2px 7px;font-size:11.5px;")
    w.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return w


def type_tag(text: str, unknown: bool = False) -> QLabel:
    w = QLabel(text)
    if unknown:
        css = f"border:1.5px solid {theme.ACCENT};color:{theme.ACCENT};"
    else:
        css = "background:#FFFFFF;color:#121212;"
    w.setStyleSheet(css + "border-radius:6px;padding:3px 8px;font-size:12px;font-weight:700;")
    w.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return w


def stat_tile(value: str, caption: str, accent: bool = False):
    f = frame("panel")
    v = QLabel(value)
    v.setStyleSheet(f"font-size:28px;font-weight:800;color:{theme.ACCENT if accent else theme.TEXT};")
    c = label(caption, muted=True)
    f.setLayout(vbox(v, c, spacing=2, margins=(18, 14, 18, 14)))
    f.value_label = v
    return f


def open_path(path):
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def pil_to_pixmap(img) -> QPixmap:
    img = img.convert("RGB")
    data = img.tobytes("raw", "RGB")
    qimg = QImage(data, img.width, img.height, 3 * img.width, QImage.Format_RGB888)
    return QPixmap.fromImage(qimg.copy())


def thumbnail(path, size: int = 120) -> Optional[QPixmap]:
    """Миниатюра картинки или первой страницы PDF."""
    path = Path(path)
    try:
        if path.suffix.lower() == ".pdf":
            import pymupdf
            with pymupdf.open(path) as doc:
                if doc.needs_pass or not doc.page_count:
                    return None
                page = doc[0]
                zoom = size / max(page.rect.width, page.rect.height)
                pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
                qimg = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
                return QPixmap.fromImage(qimg.copy())
        from ..ocr.ocr_engine import load_image
        img = load_image(path)
        img.thumbnail((size, size))
        return pil_to_pixmap(img)
    except Exception:  # noqa: BLE001
        return None


class DropZone(QFrame):
    """Зона перетаскивания файлов; по клику открывает выбор файлов."""

    files_dropped = Signal(list)

    def __init__(self, title: str, subtitle: str, extensions: Iterable[str], dialog_filter: str,
                 compact: bool = False):
        super().__init__()
        self.setProperty("drop", True)
        self.extensions = {e.lower() for e in extensions}
        self.dialog_filter = dialog_filter
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        icon = QLabel("+" if compact else "⬆")
        icon.setAlignment(Qt.AlignCenter)
        icon.setFixedSize(40, 40)
        icon.setStyleSheet(f"background:{theme.ACCENT};color:#121212;border-radius:10px;font-size:20px;font-weight:800;")
        t = label(title)
        t.setStyleSheet("font-weight:700;font-size:15px;")
        s = label(subtitle, dim=True)
        s.setWordWrap(True)
        self.setLayout(hbox(icon, vbox(t, s, spacing=2), None, spacing=14, margins=(16, 12, 16, 12)))
        self.setMinimumHeight(72)

    def _accepts(self, urls) -> List[Path]:
        out = []
        for u in urls:
            p = Path(u.toLocalFile())
            if p.is_dir():
                out += [f for f in sorted(p.iterdir()) if f.suffix.lower() in self.extensions]
            elif p.suffix.lower() in self.extensions:
                out.append(p)
        return out

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and self._accepts(e.mimeData().urls()):
            e.acceptProposedAction()
            self.setProperty("hover", True)
            repolish(self)

    def dragLeaveEvent(self, e):
        self.setProperty("hover", False)
        repolish(self)

    def dropEvent(self, e):
        self.setProperty("hover", False)
        repolish(self)
        files = self._accepts(e.mimeData().urls())
        if files:
            self.files_dropped.emit(files)

    def mousePressEvent(self, e):
        files, _ = QFileDialog.getOpenFileNames(self, "Выберите файлы", "", self.dialog_filter)
        if files:
            self.files_dropped.emit([Path(f) for f in files])


# ---------------------------------------------------------------- фоновые задачи

class _Signals(QObject):
    progress = Signal(str)
    done = Signal(object)
    failed = Signal(str)


class Task(QRunnable):
    """fn(progress=callable) выполняется в фоне; результат приходит сигналом done."""

    def __init__(self, fn: Callable, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = _Signals()

    def run(self):
        try:
            result = self.fn(*self.args, progress=self.signals.progress.emit, **self.kwargs)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.signals.failed.emit(f"{type(e).__name__}: {e}")
        else:
            self.signals.done.emit(result)


# Тяжелые задачи (OCR) — строго по одной, чтобы не перегружать компьютер.
HEAVY_POOL = QThreadPool()
HEAVY_POOL.setMaxThreadCount(1)
# Ссылки на запущенные задачи: иначе Python удалит объект сигналов раньше,
# чем окно получит результат.
_RUNNING = set()


def run_task(fn: Callable, *args, on_done=None, on_fail=None, on_progress=None, **kwargs) -> Task:
    task = Task(fn, *args, **kwargs)
    task.setAutoDelete(False)
    _RUNNING.add(task)
    if on_done:
        task.signals.done.connect(on_done)
    if on_fail:
        task.signals.failed.connect(on_fail)
    if on_progress:
        task.signals.progress.connect(on_progress)
    # отпускаем ссылку последним обработчиком
    task.signals.done.connect(lambda _r: _RUNNING.discard(task))
    task.signals.failed.connect(lambda _e: _RUNNING.discard(task))
    HEAVY_POOL.start(task)
    return task
