"""Окно «Расширение Chrome»: как установить, настройки автозаполнения."""

from PySide6.QtCore import QSettings
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog, QLineEdit, QMessageBox

from .. import browser_bridge, config
from .widgets import button, frame, hbox, label, open_path, vbox, wrap_label


def temp_number() -> str:
    value = QSettings("E9pay", "E9pay").value("kica_temp_number", browser_bridge.DEFAULT_TEMP_NUMBER)
    return str(value or browser_bridge.DEFAULT_TEMP_NUMBER)


def extension_dir():
    return config.app_dir() / "extension"


class ExtensionDialog(QDialog):
    def __init__(self, port, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Расширение Chrome — автозаполнение")
        self.setMinimumWidth(660)
        self.setMinimumHeight(640)
        status = (f"✓ Программа готова отвечать расширению (127.0.0.1:{port}, только этот компьютер)"
                  if port else "⚠ Связь с расширением не запущена — перезапустите E9pay")
        st = wrap_label(status, warn=not port)
        self.temp = QLineEdit(temp_number())
        self.temp.setMaxLength(6)
        self.temp.setFixedWidth(120)
        steps = wrap_label(
            "1. Откройте в Chrome адрес  chrome://extensions\n"
            "2. Справа вверху включите «Режим разработчика».\n"
            "3. Нажмите «Загрузить распакованное расширение» и выберите папку расширения "
            "(кнопка ниже откроет ее).\n"
            "4. Закрепите значок E9pay на панели Chrome (значок пазла → булавка).\n\n"
            "Как пользоваться: откройте заявку на сайте KICA, нажмите значок E9pay и выберите клиента. "
            "Заполнятся 이름 (строчными), 주민등록번호, а после «확인» — 임시번호, телефон, страна, адрес, e-mail. "
            "Адрес копируется в буфер — вставьте его в окне «우편번호 찾기» (Ctrl+V) и выберите строку.\n\n"
            "E9pay должна быть запущена. Данные берутся только с этого компьютера и вводятся только в открытую "
            "форму — в интернет программа их не отправляет.")
        path = str(extension_dir())
        copy_b = button("Скопировать путь", small=True,
                        on_click=lambda: QGuiApplication.clipboard().setText(path))
        self.setLayout(vbox(
            label("Расширение Chrome для сайта KICA", h2=True), st, steps,
            hbox(button("Открыть папку расширения", on_click=lambda: open_path(extension_dir())), copy_b, None),
            label(path, dim=True),
            frame_line(),
            hbox(label("발급용 임시번호 (6 цифр):"), self.temp, None),
            None,
            hbox(None, button("Закрыть", on_click=self.reject), button("Сохранить", primary=True, on_click=self.save)),
            spacing=10, margins=(20, 18, 20, 18)))

    def save(self):
        value = self.temp.text().strip()
        if not (value.isdigit() and len(value) == 6):
            QMessageBox.warning(self, "임시번호", "Нужно ровно 6 цифр, например 112233.")
            return
        QSettings("E9pay", "E9pay").setValue("kica_temp_number", value)
        self.accept()


def frame_line():
    f = frame("panel")
    f.setFixedHeight(1)
    f.setStyleSheet("background:rgba(255,255,255,0.10);border:none;")
    return f
