import base64
import json
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import raspisanie_core as core

APP_NAME = "Расписание на печать"
SETTINGS_PATH = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "RaspisaniePrint", "settings.json")
SCALE_MIN = 30
ROTATE_LABELS = ((270, "По часовой стрелке"), (90, "Против часовой"), (0, "Без поворота"))
FILE_TYPES = [
    ("Расписание", " ".join("*" + ext for ext in core.SOURCE_EXTS)),
    ("PDF", "*.pdf"),
    ("Word", " ".join("*" + ext for ext in core.WORD_EXTS)),
    ("Все файлы", "*.*"),
]


def resource_path(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def clean_settings(raw):
    cfg = {"scale": core.DEFAULT_SCALE, "rotate": core.DEFAULT_ROTATE, "open_after": True, "last_dir": ""}
    if not isinstance(raw, dict):
        return cfg
    scale = raw.get("scale")
    if isinstance(scale, (int, float)) and not isinstance(scale, bool) and SCALE_MIN / 100 <= scale <= 1:
        cfg["scale"] = float(scale)
    rotate = raw.get("rotate")
    if not isinstance(rotate, bool) and rotate in core.ROTATIONS:
        cfg["rotate"] = int(rotate)
    if isinstance(raw.get("open_after"), bool):
        cfg["open_after"] = raw["open_after"]
    last_dir = raw.get("last_dir")
    if isinstance(last_dir, str) and last_dir and os.path.isdir(last_dir):
        cfg["last_dir"] = last_dir
    return cfg


def load_settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return clean_settings(json.load(f))
    except (OSError, ValueError):
        return clean_settings(None)


def save_settings(cfg):
    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


class App:
    def __init__(self, root, initial_path=None):
        self.root = root
        self.cfg = load_settings()
        self.src = None
        self.src_path = None
        self.loading = False
        self.preview_img = None
        self.jobs = queue.Queue()
        self._preview_job = None

        self.path_var = tk.StringVar()
        self.out_var = tk.StringVar()
        self.rotate_var = tk.IntVar(value=self.cfg["rotate"])
        self.scale_var = tk.IntVar(value=round(self.cfg["scale"] * 100))
        self.open_after_var = tk.BooleanVar(value=self.cfg["open_after"])
        self.status_var = tk.StringVar(value="Выберите файл расписания: .docx или .pdf")

        root.title(APP_NAME)
        root.geometry("980x700")
        root.minsize(820, 600)
        try:
            root.iconbitmap(resource_path(os.path.join("assets", "icon.ico")))
        except tk.TclError:
            pass
        self._build_ui()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<Control-o>", lambda e: self.choose_file())
        root.bind("<Control-s>", lambda e: self.save())
        root.after(100, self._poll_jobs)
        if initial_path:
            self.open_path(initial_path)

    def _build_ui(self):
        style = ttk.Style(self.root)
        style.configure("Accent.TButton", font=("Segoe UI", 11, "bold"), padding=(12, 8))
        style.configure("Hint.TLabel", foreground="#666")
        style.configure("Error.TLabel", foreground="#b00020")

        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        side = ttk.Frame(main, width=330)
        side.grid(row=0, column=0, sticky="ns", padx=(0, 12))

        box = ttk.LabelFrame(side, text="Исходный файл", padding=8)
        box.pack(fill="x")
        self.path_entry = ttk.Entry(box, textvariable=self.path_var, state="readonly", width=40)
        self.path_entry.pack(fill="x")
        self.open_btn = ttk.Button(box, text="Выбрать файл...  (Ctrl+O)", command=self.choose_file)
        self.open_btn.pack(fill="x", pady=(6, 0))

        box = ttk.LabelFrame(side, text="Настройки", padding=8)
        box.pack(fill="x", pady=(10, 0))
        row = ttk.Frame(box)
        row.pack(fill="x")
        ttk.Label(row, text="Страница:").pack(side="left")
        self.page_spin = ttk.Spinbox(row, from_=1, to=1, width=5, command=self.schedule_preview)
        self.page_spin.set("1")
        self.page_spin.pack(side="left", padx=6)
        self.page_spin.bind("<KeyRelease>", lambda e: self.schedule_preview())
        self.pages_label = ttk.Label(row, text="из 1", style="Hint.TLabel")
        self.pages_label.pack(side="left")

        ttk.Label(box, text="Поворот таблицы:").pack(anchor="w", pady=(10, 2))
        for angle, label in ROTATE_LABELS:
            ttk.Radiobutton(box, text=label, value=angle, variable=self.rotate_var,
                            command=self.schedule_preview).pack(anchor="w")

        ttk.Label(box, text="Высота таблицы на листе:").pack(anchor="w", pady=(10, 2))
        row = ttk.Frame(box)
        row.pack(fill="x")
        ttk.Scale(row, from_=SCALE_MIN, to=100, variable=self.scale_var,
                  command=self._on_scale).pack(side="left", fill="x", expand=True)
        self.scale_label = ttk.Label(row, width=6, anchor="e")
        self.scale_label.pack(side="left")
        self._show_scale()
        ttk.Button(box, text="Сбросить настройки", command=self.reset_options).pack(anchor="w", pady=(8, 0))

        box = ttk.LabelFrame(side, text="Сохранить как", padding=8)
        box.pack(fill="x", pady=(10, 0))
        self.out_entry = ttk.Entry(box, textvariable=self.out_var, width=40)
        self.out_entry.pack(fill="x")
        ttk.Button(box, text="Изменить...", command=self.choose_output).pack(anchor="w", pady=(6, 0))
        ttk.Checkbutton(box, text="Открыть PDF после сохранения", variable=self.open_after_var).pack(anchor="w", pady=(6, 0))

        self.save_btn = ttk.Button(side, text="Сохранить PDF  (Ctrl+S)", style="Accent.TButton", command=self.save)
        self.save_btn.pack(fill="x", pady=(14, 0))
        ttk.Label(side, text="При печати выберите «Реальный размер»\nили «Без масштабирования».",
                  style="Hint.TLabel", justify="left").pack(anchor="w", pady=(8, 0))

        self.canvas = tk.Canvas(main, background="#d9dde3", highlightthickness=0)
        self.canvas.grid(row=0, column=1, sticky="nsew")
        self.canvas.bind("<Configure>", lambda e: self.schedule_preview())

        bar = ttk.Frame(main)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.status_label = ttk.Label(bar, textvariable=self.status_var)
        self.status_label.pack(side="left")
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=160)

    def set_status(self, msg, error=False):
        self.status_var.set(msg)
        self.status_label.configure(style="Error.TLabel" if error else "TLabel")

    def set_busy(self, busy, msg=""):
        self.loading = busy
        state = "disabled" if busy else "normal"
        self.open_btn.configure(state=state)
        self.save_btn.configure(state=state)
        if busy:
            self.progress.pack(side="right")
            self.progress.start(12)
            self.set_status(msg)
        else:
            self.progress.stop()
            self.progress.pack_forget()

    def _on_scale(self, raw):
        self.scale_var.set(round(float(raw)))
        self._show_scale()
        self.schedule_preview()

    def _show_scale(self):
        self.scale_label.configure(text=f"{self.scale_var.get()} %")

    def reset_options(self):
        self.scale_var.set(round(core.DEFAULT_SCALE * 100))
        self.rotate_var.set(core.DEFAULT_ROTATE)
        self._show_scale()
        self.schedule_preview()

    def read_options(self):
        try:
            page = int(self.page_spin.get())
        except ValueError:
            raise core.PrintPrepError("Номер страницы должен быть целым числом") from None
        return self.scale_var.get() / 100, self.rotate_var.get(), page - 1

    def choose_file(self):
        if self.loading:
            return
        path = filedialog.askopenfilename(parent=self.root, title="Файл расписания",
                                          initialdir=self.cfg["last_dir"] or None, filetypes=FILE_TYPES)
        if path:
            self.open_path(path)

    def choose_output(self):
        current = self.out_var.get() or (self.src_path and core.default_output_path(self.src_path)) or ""
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Куда сохранить PDF", defaultextension=".pdf",
            initialdir=os.path.dirname(current) or None, initialfile=os.path.basename(current),
            filetypes=[("PDF", "*.pdf")],
        )
        if path:
            self.out_var.set(os.path.normpath(path))
            self._show_path_ends()

    def _show_path_ends(self):
        for entry in (self.path_entry, self.out_entry):
            entry.xview_moveto(1)

    def open_path(self, path):
        if self.loading:
            return
        path = os.path.abspath(path)
        is_word = os.path.splitext(path)[1].lower() in core.WORD_EXTS
        self.set_busy(True, "Конвертирую через Word, это может занять до минуты..." if is_word else "Открываю файл...")
        threading.Thread(target=self._load_worker, args=(path,), daemon=True).start()

    def _load_worker(self, path):
        try:
            self.jobs.put(("loaded", path, core.load_source(path)))
        except Exception as e:  # the UI waits for a reply, so every failure has to reach it
            self.jobs.put(("failed", path, e))

    def _poll_jobs(self):
        try:
            while True:
                kind, path, payload = self.jobs.get_nowait()
                self.set_busy(False)
                if kind == "loaded":
                    self._on_loaded(path, payload)
                else:
                    self.set_status(f"Не удалось открыть файл: {payload}", error=True)
                    messagebox.showerror(APP_NAME, str(payload), parent=self.root)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_jobs)

    def _on_loaded(self, path, src):
        if self.src is not None:
            self.src.close()
        self.src, self.src_path = src, path
        self.path_var.set(path)
        self.out_var.set(core.default_output_path(path))
        self._show_path_ends()
        self.page_spin.configure(to=src.page_count)
        self.page_spin.set("1")
        self.pages_label.configure(text=f"из {src.page_count}")
        self.cfg["last_dir"] = os.path.dirname(path)
        self.set_status(f"Открыт: {os.path.basename(path)}, страниц: {src.page_count}")
        self.update_preview()

    def schedule_preview(self):
        if self._preview_job:
            self.root.after_cancel(self._preview_job)
        self._preview_job = self.root.after(120, self.update_preview)

    def update_preview(self):
        self._preview_job = None
        c = self.canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if self.src is None:
            c.create_text(w // 2, h // 2, text="Здесь будет предпросмотр листа", fill="#666", font=("Segoe UI", 12))
            return
        page_h = min(h - 32, (w - 32) * core.PORTRAIT_H / core.PORTRAIT_W)
        if page_h < 50:
            return
        try:
            doc = core.build_print_doc(self.src, *self.read_options())
        except core.PrintPrepError as e:
            self.set_status(str(e), error=True)
            return
        try:
            png = core.render_png(doc, page_h)
        finally:
            doc.close()
        self.preview_img = tk.PhotoImage(data=base64.b64encode(png))
        iw, ih = self.preview_img.width(), self.preview_img.height()
        x0, y0 = (w - iw) // 2, (h - ih) // 2
        c.create_rectangle(x0 + 4, y0 + 4, x0 + iw + 4, y0 + ih + 4, fill="#a9aeb6", outline="")
        c.create_image(x0, y0, image=self.preview_img, anchor="nw")
        if self.status_label.cget("style") == "Error.TLabel":
            self.set_status(f"Открыт: {os.path.basename(self.src_path)}, страниц: {self.src.page_count}")

    def save(self):
        if self.loading:
            return
        if self.src is None:
            messagebox.showinfo(APP_NAME, "Сначала выберите файл расписания.", parent=self.root)
            return
        out_path = self.out_var.get().strip()
        if not out_path:
            messagebox.showerror(APP_NAME, "Укажите, куда сохранить PDF.", parent=self.root)
            return
        try:
            scale, rotate, page_index = self.read_options()
            core.export(self.src, self.src_path, out_path, scale, rotate, page_index)
        except core.PrintPrepError as e:
            self.set_status(str(e), error=True)
            messagebox.showerror(APP_NAME, str(e), parent=self.root)
            return

        out_path = os.path.abspath(out_path)
        self.cfg.update(scale=scale, rotate=rotate, open_after=self.open_after_var.get())
        save_settings(self.cfg)
        self.set_status(f"Сохранено: {out_path}")
        if self.open_after_var.get():
            try:
                os.startfile(out_path)
            except OSError as e:
                messagebox.showwarning(APP_NAME, f"PDF сохранён, но открыть его не получилось: {e}", parent=self.root)

    def close(self):
        if self.loading and not messagebox.askyesno(
                APP_NAME, "Файл ещё конвертируется. Закрыть программу всё равно?", parent=self.root):
            return
        self.cfg.update(scale=self.scale_var.get() / 100, rotate=self.rotate_var.get(),
                        open_after=self.open_after_var.get())
        save_settings(clean_settings(self.cfg))
        if self.src is not None:
            self.src.close()
        self.root.destroy()


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    App(root, sys.argv[1] if len(sys.argv) > 1 else None)
    root.mainloop()


if __name__ == "__main__":
    main()
