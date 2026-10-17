"""Окно «Отчет OCR»: текст для отправки разработчику, номера скрыты по умолчанию."""

from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import QCheckBox, QDialog, QFileDialog, QPlainTextEdit

from .. import services
from .widgets import button, hbox, label, vbox, wrap_label


class OcrReportDialog(QDialog):
    def __init__(self, report: str, client_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Отчет OCR")
        self.resize(900, 700)
        self.report, self.client_name = report, client_name
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(QFont("Consolas", 10))
        self.text.setStyleSheet("QPlainTextEdit{background:#141414;color:#EDEDED;border:1px solid "
                                "rgba(255,255,255,0.22);border-radius:8px;padding:8px;}")
        self.mask = QCheckBox("Скрыть номера (ARC и счет)")
        self.mask.setChecked(True)
        self.mask.toggled.connect(self._render)
        self.status = label("", dim=True)
        copy_b = button("Копировать всё", primary=True, on_click=self.copy)
        save_b = button("Сохранить .txt", on_click=self.save)
        self.setLayout(vbox(
            label("Отчет OCR", h2=True),
            wrap_label("Что программа увидела на каждом фото и что из этого взяла. Скопируйте текст целиком и "
                       "отправьте вместе со скриншотом карты — так сразу видно, где ошибка.", muted=True),
            self.text, hbox(self.mask, None, self.status, save_b, copy_b),
            spacing=10, margins=(20, 16, 20, 16)))
        self._render()

    def _current(self) -> str:
        return services.mask_report(self.report) if self.mask.isChecked() else self.report

    def _render(self):
        self.text.setPlainText(self._current())

    def copy(self):
        QGuiApplication.clipboard().setText(self._current())
        self.status.setText("Скопировано ✓")

    def save(self):
        name = (self.client_name or "client").replace(" ", "_")
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить отчет", f"OCR_{name}.txt", "Текст (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._current())
            self.status.setText("Сохранено ✓")
