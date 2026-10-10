"""Окно программы: выбор фото -> распознавание -> проверка -> Excel."""

import queue
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import ImageTk

from .excel_export import COLUMNS, card_to_row, save_xlsx
from .ocr_engine import IMAGE_EXTENSIONS, load_image

STATUS_WAIT, STATUS_DONE, STATUS_ERR = "ожидает", "готово", "ошибка"
TABLE_COLUMNS = [("status", "Статус", 80)] + [(k, t, w * 7) for k, t, w in COLUMNS]
EDITABLE = {k for k, _, _ in COLUMNS} - {"file"}


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("E9pass OCR — ID-карты иностранцев → Excel")
        root.geometry("1280x760")
        self.rows = {}           # item_id -> {"path": Path, "data": dict, "lines": [...]}
        self.reader = None
        self.queue = queue.Queue()
        self.worker = None
        self._preview_img = None
        self._edit = None
        self._build()
        root.after(100, self._poll)

    # ------------------------------------------------------------ интерфейс

    def _build(self):
        bar = ttk.Frame(self.root, padding=6)
        bar.pack(fill="x")
        ttk.Button(bar, text="Добавить файлы…", command=self.add_files).pack(side="left")
        ttk.Button(bar, text="Добавить папку…", command=self.add_folder).pack(side="left", padx=4)
        ttk.Button(bar, text="Удалить выбранные", command=self.remove_selected).pack(side="left")
        self.btn_run = ttk.Button(bar, text="▶ Распознать", command=self.start_ocr)
        self.btn_run.pack(side="left", padx=12)
        ttk.Button(bar, text="Сохранить в Excel…", command=self.save_excel).pack(side="left")
        self.progress = ttk.Progressbar(bar, length=220, mode="determinate")
        self.progress.pack(side="right")
        self.status = tk.StringVar(value="Добавьте фото или сканы ID-карт (лицевая сторона).")
        ttk.Label(self.root, textvariable=self.status, padding=(8, 0)).pack(fill="x")

        paned = ttk.PanedWindow(self.root, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=6, pady=6)

        table_frame = ttk.Frame(paned)
        self.tree = ttk.Treeview(table_frame, columns=[c[0] for c in TABLE_COLUMNS],
                                 show="headings", selectmode="extended")
        for key, title, width in TABLE_COLUMNS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=40, stretch=False)
        self.tree.tag_configure("warn", background="#FFF2CC")
        self.tree.tag_configure("err", background="#F8CBAD")
        ysb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        xsb = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._on_double_click)
        paned.add(table_frame, weight=3)

        side = ttk.Frame(paned, padding=4)
        self.preview = ttk.Label(side, text="Предпросмотр", anchor="center")
        self.preview.pack(fill="both", expand=True)
        ttk.Label(side, text="Распознанный текст:").pack(anchor="w")
        self.raw_text = tk.Text(side, height=12, wrap="word")
        self.raw_text.pack(fill="x")
        paned.add(side, weight=1)

        ttk.Label(self.root, padding=(8, 0, 8, 6), foreground="#555",
                  text="Двойной щелчок по ячейке — исправить значение. Жёлтые строки — "
                       "проверьте вручную. Всё обрабатывается только на этом компьютере.").pack(fill="x")

    # ------------------------------------------------------------ файлы

    def add_files(self):
        types = [("Изображения", " ".join("*" + e for e in sorted(IMAGE_EXTENSIONS))), ("Все файлы", "*.*")]
        for p in filedialog.askopenfilenames(title="Фото ID-карт", filetypes=types):
            self._add(Path(p))

    def add_folder(self):
        folder = filedialog.askdirectory(title="Папка с фото ID-карт")
        if folder:
            for p in sorted(Path(folder).iterdir()):
                if p.suffix.lower() in IMAGE_EXTENSIONS:
                    self._add(p)

    def _add(self, path: Path):
        if any(r["path"] == path for r in self.rows.values()):
            return
        item = self.tree.insert("", "end", values=self._values(STATUS_WAIT, {"file": path.name}))
        self.rows[item] = {"path": path, "data": {"file": path.name}, "lines": [], "status": STATUS_WAIT}
        self.status.set(f"Файлов в списке: {len(self.rows)}")

    def remove_selected(self):
        if self.worker and self.worker.is_alive():
            return
        for item in self.tree.selection():
            self.tree.delete(item)
            self.rows.pop(item, None)

    def _values(self, status, data):
        out = [status]
        for key, _, _ in COLUMNS:
            v = data.get(key, "")
            out.append("; ".join(v) if isinstance(v, list) else v)
        return out

    # ------------------------------------------------------------ OCR

    def start_ocr(self):
        if self.worker and self.worker.is_alive():
            return
        todo = [i for i, r in self.rows.items() if r["status"] != STATUS_DONE]
        if not todo:
            messagebox.showinfo("Нечего распознавать", "Добавьте файлы или все уже распознаны.")
            return
        self.btn_run.state(["disabled"])
        self.progress.configure(maximum=len(todo), value=0)
        jobs = [(i, self.rows[i]["path"]) for i in todo]
        self.worker = threading.Thread(target=self._work, args=(jobs,), daemon=True)
        self.worker.start()

    def _work(self, jobs):
        try:
            if self.reader is None:
                self.queue.put(("status", "Загрузка модели распознавания (первый раз — до минуты)…"))
                from .ocr_engine import CardReader
                self.reader = CardReader()
        except Exception as e:  # noqa: BLE001
            self.queue.put(("fatal", f"Не удалось загрузить OCR: {e}"))
            return
        for n, (item, path) in enumerate(jobs, start=1):
            self.queue.put(("status", f"Распознаю {n}/{len(jobs)}: {path.name}"))
            try:
                data, lines = self.reader.read(path)
                self.queue.put(("result", item, card_to_row(path.name, data), lines))
            except Exception as e:  # noqa: BLE001
                self.queue.put(("error", item, str(e)))
        self.queue.put(("finished", len(jobs)))

    def _poll(self):
        try:
            while True:
                msg = self.queue.get_nowait()
                kind = msg[0]
                if kind == "status":
                    self.status.set(msg[1])
                elif kind == "result":
                    _, item, row, lines = msg
                    if item in self.rows:
                        self.rows[item].update(data=row, lines=lines, status=STATUS_DONE)
                        self._refresh(item)
                    self.progress.step(1)
                elif kind == "error":
                    _, item, err = msg
                    if item in self.rows:
                        r = self.rows[item]
                        r["status"] = STATUS_ERR
                        r["data"]["warnings"] = [f"ошибка: {err}"]
                        self._refresh(item)
                    self.progress.step(1)
                elif kind == "finished":
                    warn = sum(1 for r in self.rows.values() if r["data"].get("warnings"))
                    self.status.set(f"Готово: {msg[1]} файл(ов). Требуют проверки: {warn}.")
                    self.btn_run.state(["!disabled"])
                elif kind == "fatal":
                    self.status.set(msg[1])
                    self.btn_run.state(["!disabled"])
                    messagebox.showerror("Ошибка", msg[1])
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _refresh(self, item):
        r = self.rows[item]
        tag = "err" if r["status"] == STATUS_ERR else ("warn" if r["data"].get("warnings") else "")
        self.tree.item(item, values=self._values(r["status"], r["data"]), tags=(tag,) if tag else ())
        if self.tree.selection() == (item,):
            self._show(item)

    # ------------------------------------------------------------ просмотр и правка

    def _on_select(self, _event=None):
        sel = self.tree.selection()
        if len(sel) == 1:
            self._show(sel[0])

    def _show(self, item):
        r = self.rows[item]
        try:
            img = load_image(r["path"])
            w = max(200, self.preview.winfo_width() - 8)
            img.thumbnail((w, w))
            self._preview_img = ImageTk.PhotoImage(img)
            self.preview.configure(image=self._preview_img, text="")
        except Exception as e:  # noqa: BLE001
            self.preview.configure(image="", text=f"Не удалось открыть: {e}")
        self.raw_text.delete("1.0", "end")
        self.raw_text.insert("1.0", "\n".join(r["lines"]))

    def _on_double_click(self, event):
        if self.tree.identify("region", event.x, event.y) != "cell":
            return
        item = self.tree.identify_row(event.y)
        col_index = int(self.tree.identify_column(event.x)[1:]) - 1
        key = TABLE_COLUMNS[col_index][0]
        if key not in EDITABLE or not item:
            return
        x, y, w, h = self.tree.bbox(item, f"#{col_index + 1}")
        value = self.tree.set(item, key)
        entry = ttk.Entry(self.tree)
        entry.insert(0, value)
        entry.select_range(0, "end")
        entry.place(x=x, y=y, width=max(w, 120), height=h)
        entry.focus_set()

        def commit(_e=None):
            new = entry.get().strip()
            r = self.rows[item]
            r["data"][key] = [new] if key == "warnings" and new else ([] if key == "warnings" else new)
            entry.destroy()
            self._refresh(item)

        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)
        entry.bind("<Escape>", lambda _e: entry.destroy())

    # ------------------------------------------------------------ Excel

    def save_excel(self):
        if not self.rows:
            messagebox.showinfo("Нет данных", "Сначала добавьте и распознайте файлы.")
            return
        not_done = sum(1 for r in self.rows.values() if r["status"] != STATUS_DONE)
        if not_done and not messagebox.askyesno(
                "Не всё распознано", f"{not_done} файл(ов) не распознаны. Сохранить всё равно?"):
            return
        default = f"id_cards_{datetime.now():%Y%m%d_%H%M}.xlsx"
        path = filedialog.asksaveasfilename(defaultextension=".xlsx", initialfile=default,
                                            filetypes=[("Excel", "*.xlsx")])
        if not path:
            return
        rows = [self.rows[i]["data"] for i in self.tree.get_children()]
        try:
            save_xlsx(rows, path)
        except PermissionError:
            messagebox.showerror("Ошибка", "Файл открыт в Excel или нет доступа. Закройте его и повторите.")
            return
        self.status.set(f"Сохранено: {path}")
        if messagebox.askyesno("Сохранено", "Файл сохранён. Открыть?"):
            import os
            import subprocess
            import sys
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])


def main():
    root = tk.Tk()
    try:
        ttk.Style(root).theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
