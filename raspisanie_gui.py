"""Окно «Расписание на печать» на Qt (PySide6): предпросмотр, настройки, сохранение."""
import json
import os
import queue
import sys
import threading

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QFileDialog, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton,
                               QRadioButton, QSlider, QSpinBox, QStyleFactory, QVBoxLayout, QWidget)

import raspisanie_core as core

APP_NAME = "Расписание на печать"
SETTINGS_PATH = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "RaspisaniePrint", "settings.json")
SCALE_MIN = 30
ROTATE_LABELS = ((270, "По часовой стрелке"), (90, "Против часовой"), (0, "Без поворота"))
FILE_FILTER = ("Расписание (" + " ".join("*" + ext for ext in core.SOURCE_EXTS) + ");;"
               "PDF (*.pdf);;Word (" + " ".join("*" + ext for ext in core.WORD_EXTS) + ");;Все файлы (*.*)")
PREVIEW_DELAY_MS = 120
POLL_MS = 100
UI_FONT_SIZE = 10
STATUS_READY = "Выберите файл расписания: .docx или .pdf (или перетащите его в окно)"
_translators = []  # Qt хранит только указатель на переводчик — держим объект живым


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


class dialogs:  # noqa: N801 — пространство имён: тесты подменяют эти функции
    @staticmethod
    def info(parent, text):
        QMessageBox.information(parent, APP_NAME, text)

    @staticmethod
    def warning(parent, text):
        QMessageBox.warning(parent, APP_NAME, text)

    @staticmethod
    def error(parent, text):
        QMessageBox.critical(parent, APP_NAME, text)

    @staticmethod
    def yes_no(parent, text):
        return QMessageBox.question(parent, APP_NAME, text) == QMessageBox.Yes

    @staticmethod
    def open_path(parent, start_dir):
        return QFileDialog.getOpenFileName(parent, "Файл расписания", start_dir, FILE_FILTER)[0]

    @staticmethod
    def save_path(parent, suggested):
        return QFileDialog.getSaveFileName(parent, "Куда сохранить PDF", suggested, "PDF (*.pdf)")[0]

    @staticmethod
    def open_file(path):
        os.startfile(path)  # noqa: S606 (только Windows)


class Preview(QWidget):
    """Лист A4 с тенью. Готовая картинка сразу масштабируется под новый размер,
    а чёткая рендерится заново, когда размер перестал меняться."""

    BG = QColor("#d9dde3")
    SHADOW = QColor("#a9aeb6")
    MARGIN = 16

    def __init__(self, on_resized, parent=None):
        super().__init__(parent)
        self.image = None  # QImage последнего рендера
        self.on_resized = on_resized
        self.setMinimumSize(200, 200)

    def page_height(self):
        """Высота листа, который помещается в виджет целиком (книжный A4)."""
        w, h = self.width(), self.height()
        return min(h - 2 * self.MARGIN, (w - 2 * self.MARGIN) * core.PORTRAIT_H / core.PORTRAIT_W)

    def page_rect(self):
        ph = self.page_height()
        pw = ph * core.PORTRAIT_W / core.PORTRAIT_H
        return QRectF((self.width() - pw) / 2, (self.height() - ph) / 2, pw, ph)

    def set_image(self, image):
        self.image = image
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.on_resized()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.fillRect(self.rect(), self.BG)
        if self.image is None:
            p.setPen(QColor("#555b63"))
            f = QFont(self.font())
            f.setPointSizeF(f.pointSizeF() + 2)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignCenter, "Здесь будет предпросмотр листа")
            p.end()
            return
        if self.page_height() < 50:
            p.end()
            return
        r = self.page_rect()
        p.fillRect(r.translated(4, 4), self.SHADOW)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawImage(r, self.image)
        p.end()


class MainWindow(QMainWindow):
    def __init__(self, initial_path=None):
        super().__init__()
        self.cfg = load_settings()
        self.src = None
        self.src_path = None
        self.loading = False
        self.error = False
        self.jobs = queue.Queue()
        self.closed = False

        self.setWindowTitle(APP_NAME)
        self.resize(980, 700)
        self.setMinimumSize(820, 600)
        self.setAcceptDrops(True)
        self._build_ui()

        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(PREVIEW_DELAY_MS)
        self.preview_timer.timeout.connect(self.update_preview)
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(POLL_MS)
        self.poll_timer.timeout.connect(self._poll_jobs)
        self.poll_timer.start()

        self.open_shortcut = QShortcut(QKeySequence("Ctrl+O"), self, self.choose_file)
        self.save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self, self.save)
        if initial_path:
            self.open_path(initial_path)

    # ---- интерфейс -------------------------------------------------------------------

    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 8)
        body = QHBoxLayout()
        root.addLayout(body, 1)
        self.setCentralWidget(central)

        side = QWidget()
        side.setFixedWidth(330)
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(0, 0, 0, 0)
        body.addWidget(side)

        box = QGroupBox("Исходный файл")
        lay = QVBoxLayout(box)
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.open_btn = QPushButton("Выбрать файл…  (Ctrl+O)")
        self.open_btn.clicked.connect(self.choose_file)
        lay.addWidget(self.path_edit)
        lay.addWidget(self.open_btn)
        side_lay.addWidget(box)

        box = QGroupBox("Настройки")
        lay = QVBoxLayout(box)
        row = QHBoxLayout()
        row.addWidget(QLabel("Страница:"))
        self.page_spin = QSpinBox()
        self.page_spin.setRange(1, 1)
        self.page_spin.valueChanged.connect(lambda _v: self.schedule_preview())
        row.addWidget(self.page_spin)
        self.pages_label = QLabel("из 1")
        row.addWidget(self.pages_label)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addSpacing(6)
        lay.addWidget(QLabel("Поворот таблицы:"))
        self.rotate_group = QButtonGroup(self)
        self.rotate_buttons = {}
        for angle, label in ROTATE_LABELS:
            rb = QRadioButton(label)
            self.rotate_group.addButton(rb, angle)
            self.rotate_buttons[angle] = rb
            lay.addWidget(rb)
        self.rotate_buttons[self.cfg["rotate"]].setChecked(True)
        self.rotate_group.idToggled.connect(lambda _id, on: on and self.schedule_preview())
        lay.addSpacing(6)
        lay.addWidget(QLabel("Высота таблицы на листе:"))
        row = QHBoxLayout()
        self.scale_slider = QSlider(Qt.Horizontal)
        self.scale_slider.setRange(SCALE_MIN, 100)
        self.scale_slider.setValue(round(self.cfg["scale"] * 100))
        self.scale_label = QLabel()
        self.scale_label.setMinimumWidth(48)
        self.scale_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.scale_slider.valueChanged.connect(self._on_scale)
        row.addWidget(self.scale_slider, 1)
        row.addWidget(self.scale_label)
        lay.addLayout(row)
        self._show_scale()
        self.reset_btn = QPushButton("Сбросить настройки")
        self.reset_btn.clicked.connect(self.reset_options)
        row = QHBoxLayout()
        row.addWidget(self.reset_btn)
        row.addStretch(1)
        lay.addLayout(row)
        side_lay.addWidget(box)

        box = QGroupBox("Сохранить как")
        lay = QVBoxLayout(box)
        self.out_edit = QLineEdit()
        self.change_btn = QPushButton("Изменить…")
        self.change_btn.clicked.connect(self.choose_output)
        self.open_after = QCheckBox("Открыть PDF после сохранения")
        self.open_after.setChecked(self.cfg["open_after"])
        row = QHBoxLayout()
        row.addWidget(self.change_btn)
        row.addStretch(1)
        lay.addWidget(self.out_edit)
        lay.addLayout(row)
        lay.addWidget(self.open_after)
        side_lay.addWidget(box)

        self.save_btn = QPushButton("Сохранить PDF  (Ctrl+S)")
        f = QFont(self.save_btn.font())
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() + 1)
        self.save_btn.setFont(f)
        self.save_btn.setMinimumHeight(40)
        self.save_btn.clicked.connect(self.save)
        side_lay.addSpacing(8)
        side_lay.addWidget(self.save_btn)
        hint = QLabel("При печати выберите «Реальный размер»\nили «Без масштабирования».")
        hint.setEnabled(False)  # серый текст в любой теме
        side_lay.addWidget(hint)
        side_lay.addStretch(1)

        self.preview = Preview(self.schedule_preview)
        body.addWidget(self.preview, 1)

        bar = QHBoxLayout()
        self.status_label = QLabel(STATUS_READY)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # бегущая полоска
        self.progress.setFixedWidth(160)
        self.progress.hide()
        bar.addWidget(self.status_label, 1)
        bar.addWidget(self.progress)
        root.addLayout(bar)

    def set_status(self, msg, error=False):
        self.error = error
        self.status_label.setText(msg)
        self.status_label.setStyleSheet("color: #d0342c;" if error else "")

    def set_busy(self, busy, msg=""):
        self.loading = busy
        self.open_btn.setEnabled(not busy)
        self.save_btn.setEnabled(not busy)
        self.progress.setVisible(busy)
        if busy:
            self.set_status(msg)

    def _on_scale(self, _value):
        self._show_scale()
        self.schedule_preview()

    def _show_scale(self):
        self.scale_label.setText(f"{self.scale_slider.value()} %")

    @property
    def rotate(self):
        return self.rotate_group.checkedId()

    def set_rotate(self, angle):
        self.rotate_buttons[angle].setChecked(True)

    def reset_options(self):
        self.scale_slider.setValue(round(core.DEFAULT_SCALE * 100))
        self.set_rotate(core.DEFAULT_ROTATE)
        self._show_scale()
        self.schedule_preview()

    def read_options(self):
        return self.scale_slider.value() / 100, self.rotate, self.page_spin.value() - 1

    # ---- файлы ---------------------------------------------------------------------

    def choose_file(self):
        if self.loading:
            return
        path = dialogs.open_path(self, self.cfg["last_dir"] or "")
        if path:
            self.open_path(path)

    def choose_output(self):
        current = self.out_edit.text() or (self.src_path and core.default_output_path(self.src_path)) or ""
        path = dialogs.save_path(self, current)
        if path:
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
            self.out_edit.setText(os.path.normpath(path))
            self._show_path_ends()

    def _show_path_ends(self):
        for edit in (self.path_edit, self.out_edit):
            edit.setCursorPosition(len(edit.text()))

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
        except Exception as e:  # окно ждёт ответа, поэтому до него должна дойти любая ошибка
            self.jobs.put(("failed", path, e))

    def _poll_jobs(self):
        try:
            while True:
                kind, path, payload = self.jobs.get_nowait()
                self.set_busy(False)
                if self.closed:
                    if kind == "loaded":
                        payload.close()
                    continue
                if kind == "loaded":
                    self._on_loaded(path, payload)
                else:
                    self.set_status(f"Не удалось открыть файл: {payload}", error=True)
                    dialogs.error(self, str(payload))
        except queue.Empty:
            pass

    def _on_loaded(self, path, src):
        if self.src is not None:
            self.src.close()
        self.src, self.src_path = src, path
        self.path_edit.setText(path)
        self.out_edit.setText(core.default_output_path(path))
        self._show_path_ends()
        self.page_spin.blockSignals(True)
        self.page_spin.setRange(1, src.page_count)
        self.page_spin.setValue(1)
        self.page_spin.blockSignals(False)
        self.pages_label.setText(f"из {src.page_count}")
        self.cfg["last_dir"] = os.path.dirname(path)
        self.set_status(f"Открыт: {os.path.basename(path)}, страниц: {src.page_count}")
        self.update_preview()

    # ---- перетаскивание --------------------------------------------------------------

    @staticmethod
    def _dropped_path(mime):
        urls = [u for u in mime.urls() if u.isLocalFile()] if mime.hasUrls() else []
        if len(urls) != 1:
            return None
        path = urls[0].toLocalFile()
        return path if os.path.splitext(path)[1].lower() in core.SOURCE_EXTS else None

    def dragEnterEvent(self, event):
        if not self.loading and self._dropped_path(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        path = self._dropped_path(event.mimeData())
        if path and not self.loading:
            event.acceptProposedAction()
            self.open_path(path)

    # ---- предпросмотр ----------------------------------------------------------------

    def schedule_preview(self):
        self.preview_timer.start()  # перезапуск: рендер после последнего изменения

    def update_preview(self):
        self.preview_timer.stop()
        if self.src is None:
            self.preview.set_image(None)
            return
        page_h = self.preview.page_height() * self.preview.devicePixelRatioF()
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
        image = QImage.fromData(png, "PNG")
        self.preview.set_image(image)
        if self.error:
            self.set_status(f"Открыт: {os.path.basename(self.src_path)}, страниц: {self.src.page_count}")

    # ---- сохранение ------------------------------------------------------------------

    def save(self):
        if self.loading:
            return
        if self.src is None:
            dialogs.info(self, "Сначала выберите файл расписания.")
            return
        out_path = self.out_edit.text().strip()
        if not out_path:
            dialogs.error(self, "Укажите, куда сохранить PDF.")
            return
        try:
            scale, rotate, page_index = self.read_options()
            core.export(self.src, self.src_path, out_path, scale, rotate, page_index)
        except core.PrintPrepError as e:
            self.set_status(str(e), error=True)
            dialogs.error(self, str(e))
            return

        out_path = os.path.abspath(out_path)
        self.cfg.update(scale=scale, rotate=rotate, open_after=self.open_after.isChecked())
        save_settings(clean_settings(self.cfg))
        self.set_status(f"Сохранено: {out_path}")
        if self.open_after.isChecked():
            try:
                dialogs.open_file(out_path)
            except OSError as e:
                dialogs.warning(self, f"PDF сохранён, но открыть его не получилось: {e}")

    def closeEvent(self, event):
        if self.loading and not dialogs.yes_no(self, "Файл ещё конвертируется. Закрыть программу всё равно?"):
            event.ignore()
            return
        self.closed = True
        self.preview_timer.stop()
        self.cfg.update(scale=self.scale_slider.value() / 100, rotate=self.rotate,
                        open_after=self.open_after.isChecked())
        save_settings(clean_settings(self.cfg))
        if self.src is not None:
            self.src.close()
            self.src = None
        event.accept()


def create_app():
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    if "windows11" in [k.lower() for k in QStyleFactory.keys()]:
        app.setStyle("windows11")  # на Windows 10 Qt сам возьмёт windowsvista
    font = QFont(app.font())
    font.setPointSize(UI_FONT_SIZE)
    app.setFont(font)
    icon = resource_path(os.path.join("assets", "icon.ico"))
    if os.path.isfile(icon):
        app.setWindowIcon(QIcon(icon))
    if not _translators:
        tr = QTranslator(app)
        # стандартные кнопки («Да», «Отмена») и окна выбора файла — из каталога самого Qt
        if tr.load(QLocale(QLocale.Russian), "qtbase", "_", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
            app.installTranslator(tr)
            _translators.append(tr)
    return app


def main():
    app = create_app()
    win = MainWindow(sys.argv[1] if len(sys.argv) > 1 else None)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
