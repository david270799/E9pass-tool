"""Окна передачи клиентов: выгрузка в файл .e9pay и прием такого файла."""

from pathlib import Path

from PySide6.QtWidgets import (QDialog, QFileDialog, QFrame, QInputDialog, QLineEdit, QMessageBox, QScrollArea,
                               QWidget)

from .. import transfer
from .widgets import NoWheelComboBox, button, hbox, label, run_task, vbox, wrap_label

ACTIONS = [(transfer.ACTION_REPLACE, "Заменить прежнего"), (transfer.ACTION_MERGE, "Добавить новые документы к прежнему"),
           (transfer.ACTION_SKIP, "Не принимать")]


class ExportDialog(QDialog):
    def __init__(self, count: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Передать клиентов")
        self.setMinimumWidth(520)
        self.pw1 = QLineEdit()
        self.pw1.setEchoMode(QLineEdit.Password)
        self.pw1.setPlaceholderText("без пароля")
        self.pw2 = QLineEdit()
        self.pw2.setEchoMode(QLineEdit.Password)
        self.pw2.setPlaceholderText("повторите пароль")
        self.setLayout(vbox(
            label(f"Передать клиентов: {count}", h2=True),
            wrap_label("В файл попадут данные клиентов и все их документы (оригиналы, сканы, Master-PDF). "
                       "Получатель просто перетащит файл в свою программу E9pay — заново ничего не распознается.",
                       muted=True),
            label("Пароль на файл (рекомендуется — в файле личные данные и номера счетов)", dim=True),
            self.pw1, self.pw2,
            wrap_label("Пароль сообщите коллеге отдельно — не в том же сообщении, что и файл.", dim=True),
            None,
            hbox(None, button("Отмена", on_click=self.reject),
                 button("Сохранить файл…", primary=True, on_click=self._ok)),
            spacing=10, margins=(20, 18, 20, 18)))

    def _ok(self):
        if self.pw1.text() != self.pw2.text():
            QMessageBox.warning(self, "Пароль", "Пароли не совпадают.")
            return
        self.accept()

    @property
    def password(self) -> str:
        return self.pw1.text()


def export_clients_flow(parent, state, client_ids):
    if not client_ids:
        QMessageBox.information(parent, "Передать клиентов", "Отметьте галочками клиентов, которых нужно передать.")
        return
    dlg = ExportDialog(len(client_ids), parent)
    if not dlg.exec():
        return
    default = f"E9pay_{state.period}_клиентов-{len(client_ids)}{transfer.EXTENSION}"
    path, _ = QFileDialog.getSaveFileName(parent, "Куда сохранить файл передачи", default,
                                          f"Файл передачи E9pay (*{transfer.EXTENSION})")
    if not path:
        return
    state.busy_changed.emit("Сохраняю файл передачи…")
    run_task(transfer.export_clients, state.db, client_ids, Path(path), dlg.password,
             on_progress=state.busy_changed.emit,
             on_done=lambda out: (state.busy_changed.emit(""), QMessageBox.information(
                 parent, "Готово", f"Файл передачи сохранен:\n{out}\n\n"
                 + ("Он защищен паролем." if dlg.password else "Файл без пароля."))),
             on_fail=lambda err: (state.busy_changed.emit(""), QMessageBox.warning(parent, "Ошибка", err)))


class ImportDialog(QDialog):
    """Список клиентов из файла; для повторов (меньше года) — выбор действия."""

    def __init__(self, pkg: transfer.Package, parent=None):
        super().__init__(parent)
        self.pkg = pkg
        self.setWindowTitle("Принять клиентов")
        self.resize(820, 600)
        rows = vbox(spacing=8)
        dup_count = 0
        for ic in pkg.clients:
            box = QFrame()
            box.setObjectName("ImpRow")
            name = label(ic.title)
            name.setStyleSheet("font-weight:700;")
            lay = vbox(name, spacing=6, margins=(14, 10, 14, 10))
            if ic.duplicates:
                dup_count += 1
                box.setStyleSheet("QFrame#ImpRow{border:1.5px solid #F5C518;border-radius:10px;}")
                where = "\n".join(f"• {transfer_desc(c)}" for c in ic.duplicates)
                lay.addWidget(wrap_label(f"⚠ Этот клиент уже есть у вас (добавлен меньше года назад):\n{where}",
                                         warn=True))
                combo = NoWheelComboBox()
                for code, text in ACTIONS:
                    combo.addItem(text, code)
                combo.currentIndexChanged.connect(lambda _i, cb=combo, item=ic: setattr(item, "action", cb.currentData()))
                lay.addLayout(hbox(label("Что сделать:", muted=True), combo, None))
            else:
                box.setStyleSheet("QFrame#ImpRow{border:1px solid rgba(255,255,255,0.14);border-radius:10px;}")
                note = "новый клиент"
                if ic.old_duplicates:
                    note += f" · ранее добавлялся {(ic.old_duplicates[0].created_at or '')[:10]} (больше года назад)"
                lay.addWidget(label(f"✓ {note} · документов: {len(ic.data.get('documents', []))}", muted=True))
            box.setLayout(lay)
            rows.addWidget(box)
        rows.addStretch(1)
        host = QWidget()
        host.setLayout(rows)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(host)
        head = wrap_label(f"Файл {pkg.path.name} · {pkg.info} · клиентов: {len(pkg.clients)}"
                          + (f" · уже есть у вас: {dup_count}" if dup_count else ""), muted=True)
        self.setLayout(vbox(label("Принять клиентов", h2=True), head, scroll,
                            hbox(None, button("Отмена", on_click=self.reject),
                                 button("Принять", primary=True, on_click=self.accept)),
                            spacing=10, margins=(20, 18, 20, 18)))


def transfer_desc(c) -> str:
    return (f"{c.full_name or 'без имени'}, добавлен {(c.created_at or '')[:10]}, период {c.batch_period}")


def import_package_flow(parent, state, path: Path):
    password = ""
    while True:
        try:
            pkg = transfer.read_package(state.db, path, password)
            break
        except transfer.PasswordNeeded as e:
            password, ok = QInputDialog.getText(parent, "Пароль", f"{e}. Введите пароль файла передачи:",
                                                QLineEdit.Password)
            if not ok:
                return
        except transfer.TransferError as e:
            QMessageBox.warning(parent, "Не удалось открыть", str(e))
            return
    if not pkg.clients:
        QMessageBox.information(parent, "Принять клиентов", "В файле нет клиентов.")
        return
    if not ImportDialog(pkg, parent).exec():
        return
    state.busy_changed.emit("Принимаю клиентов…")

    def done(ids):
        state.busy_changed.emit("")
        periods = sorted({state.db.client(i).batch_period for i in ids if state.db.client(i)})
        if periods and state.period not in periods:
            state.set_period(periods[-1])
        state.data_changed.emit()
        QMessageBox.information(parent, "Готово", f"Принято клиентов: {len(ids)}"
                                + (f"\nПериод: {', '.join(periods)}" if periods else ""))

    run_task(transfer.import_package, state.db, pkg, on_progress=state.busy_changed.emit, on_done=done,
             on_fail=lambda err: (state.busy_changed.emit(""), QMessageBox.warning(parent, "Ошибка", err)))


def choose_and_import(parent, state):
    path, _ = QFileDialog.getOpenFileName(parent, "Файл передачи от коллеги", "",
                                          f"Файл передачи E9pay (*{transfer.EXTENSION})")
    if path:
        import_package_flow(parent, state, Path(path))
