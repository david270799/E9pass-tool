"""Окно «Обучение модели»: установка инструментов, дообучение, итог «было/стало», откат."""

from PySide6.QtWidgets import QDialog, QMessageBox

from .. import ocr_trainer, training
from .widgets import BusyBar, button, frame, hbox, label, run_task, vbox, wrap_label


class TrainDialog(QDialog):
    def __init__(self, store: training.TrainingStore, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Обучение модели")
        self.resize(760, 560)
        self.cancelled = False
        self.running = False

        self.info = wrap_label("", muted=True)
        self.models_box = vbox(spacing=6)
        mb = frame("panel")
        mb.setLayout(self.models_box)
        self.models_box.setContentsMargins(16, 12, 16, 12)
        self.busy = BusyBar()
        self.busy.hide()
        self.status = wrap_label("", dim=True)
        self.setup_b = button("Установить инструменты (~2 ГБ, один раз)", on_click=self.setup)
        self.train_b = button("Начать обучение", primary=True, on_click=self.train)
        self.stop_b = button("Остановить", on_click=self.stop)
        self.stop_b.hide()
        self.setLayout(vbox(
            label("Обучение модели", h2=True),
            wrap_label("Модели распознавания дообучаются на ваших проверенных картах прямо на этом компьютере. "
                       "Каждая пятая карта откладывается для проверки. Новая модель подключается, только если "
                       "читает точнее текущей; прежнюю всегда можно вернуть.", muted=True),
            self.info, mb, self.busy, self.status, None,
            hbox(self.setup_b, None, self.stop_b, button("Закрыть", on_click=self.reject), self.train_b),
            spacing=10, margins=(20, 16, 20, 16)))
        self.refresh()

    def refresh(self):
        st = self.store.stats()
        ready = ocr_trainer.toolkit_ready()
        self.info.setText(
            f"Проверено карт: {st[training.DONE]} (лицо {st['front']}, оборот {st['back']}). "
            + ("Инструменты обучения установлены." if ready else
               "Инструменты обучения еще не установлены — нужен интернет один раз (~2 ГБ), дальше все офлайн.")
            + " Для заметного эффекта — от 100 карт, для хорошего — 300–400.")
        while self.models_box.count():
            item = self.models_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                while item.layout().count():
                    w = item.layout().takeAt(0).widget()
                    if w:
                        w.deleteLater()
        hist = ocr_trainer.history()
        for key, m in ocr_trainer.MODELS.items():
            h = hist.get(key)
            if ocr_trainer.active_model(key) and h:
                text = (f"дообучена {h['date'].replace('T', ' ')} · точность на проверочных картах "
                        f"{h['before'] * 100:.0f}% → {h['after'] * 100:.0f}%")
            else:
                text = "стандартная"
            back = button("Вернуть прежнюю", small=True, on_click=lambda _=False, k=key: self.rollback(k))
            back.setVisible(ocr_trainer.active_model(key) is not None)
            self.models_box.addLayout(hbox(label(m["title"]), None, label(text, dim=True), back, spacing=10))
        self.setup_b.setVisible(not ready)
        self.train_b.setEnabled(ready and not self.running)
        self.setup_b.setEnabled(not self.running)

    def _start(self, title):
        self.running, self.cancelled = True, False
        self.busy.show()
        self.busy.start(title, "")
        self.stop_b.show()
        self.refresh()

    def _finish(self):
        self.running = False
        self.busy.stop()
        self.busy.hide()
        self.stop_b.hide()
        self.refresh()

    def _progress(self, msg):
        self.busy.set_text(msg)
        self.status.setText(msg)

    def stop(self):
        self.cancelled = True
        self.status.setText("Останавливаю…")

    def setup(self):
        self._start("Установка инструментов обучения")

        def done(_r):
            self._finish()
            self.status.setText("Инструменты установлены — можно обучать.")

        def failed(err):
            self._finish()
            QMessageBox.warning(self, "Установка", err)

        run_task(ocr_trainer.setup_toolkit, cancel=lambda: self.cancelled, on_done=done, on_fail=failed,
                 on_progress=self._progress)

    def train(self):
        st = self.store.stats()
        if st[training.DONE] < 20:
            QMessageBox.information(self, "Обучение", "Слишком мало проверенных карт — разметьте хотя бы 20, "
                                                      "а лучше от 100.")
            return
        self._start("Обучение модели")

        def done(results):
            self._finish()
            lines = []
            for key, r in results.items():
                title = ocr_trainer.MODELS[key]["title"]
                if "skipped" in r:
                    lines.append(f"• {title}: пропущено — {r['skipped']}")
                else:
                    verdict = "подключена ✓" if r["installed"] else "не подключена (не стала точнее)"
                    lines.append(f"• {title}: {r['before'] * 100:.0f}% → {r['after'] * 100:.0f}% верно — {verdict}"
                                 f"\n   (обучение: {r['train']} строк, проверка: {r['val']} строк)")
            small = [r["val"] for r in results.values() if "val" in r and r["val"] < 50]
            if small:
                lines.append(f"\nПроверочных строк мало — одна строка ≈ {100 / max(min(small), 1):.0f}%, поэтому "
                             "проценты заметно меняются от запуска к запуску. С ростом числа карт они станут точнее.")
            QMessageBox.information(self, "Итог обучения", "\n".join(lines))

        def failed(err):
            self._finish()
            QMessageBox.warning(self, "Обучение", err)

        run_task(ocr_trainer.train_all, self.store, cancel=lambda: self.cancelled, on_done=done, on_fail=failed,
                 on_progress=self._progress)

    def rollback(self, key):
        if QMessageBox.question(self, "Вернуть прежнюю", f"{ocr_trainer.MODELS[key]['title']}: вернуть прежнюю "
                                "модель?", QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            msg = ocr_trainer.rollback(key)
            self.status.setText(msg)
            self.refresh()

    def reject(self):
        if self.running and QMessageBox.question(
                self, "Обучение идет", "Остановить обучение и закрыть окно?",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self.cancelled = True
        super().reject()
