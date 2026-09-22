"""Снимки окна для README: собираются программой, а не руками.

    .venv\\Scripts\\python tools\\make_screenshots.py

Скрипт делает образец расписания (обычный альбомный лист A4 с таблицей уроков, без
чьих-либо настоящих данных), открывает его в настоящем окне программы и снимает само
окно через QWidget.grab().

Снимок экрана по прямоугольнику окна не годится: окно может оказаться позади других, и
в кадр попадёт чужое содержимое. grab() рисует виджет в картинку независимо от того, что
на экране, и работает даже с платформой offscreen.

Word для этого не нужен: образец сразу PDF, а PDF программа открывает сама.
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"
sys.path.insert(0, str(ROOT))

import fitz  # noqa: E402  (pymupdf)
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import raspisanie_gui as gui  # noqa: E402

LESSONS = [
    ("1", "8:30", "Алгебра", "каб. 204"),
    ("2", "9:25", "Русский язык", "каб. 112"),
    ("3", "10:30", "Физика", "каб. 301"),
    ("4", "11:25", "История", "каб. 208"),
    ("5", "12:30", "Английский язык", "каб. 115"),
    ("6", "13:25", "Физкультура", "спортзал"),
]


# Встроенные шрифты PDF кириллицы не знают: с fontname="helv" русские строки молча
# исчезают, и в предпросмотре остаётся пустая таблица. Берём системный Arial.
FONT = "C:/Windows/Fonts/arial.ttf"
FONT_BOLD = "C:/Windows/Fonts/arialbd.ttf"


def sample_pdf(path: str) -> None:
    """Альбомный A4 с таблицей уроков - то, ради чего программа и написана."""
    doc = fitz.open()
    page = doc.new_page(width=842, height=595)  # A4 landscape, points
    page.insert_text((60, 70), "Расписание на неделю", fontsize=24, fontname="bold", fontfile=FONT_BOLD)
    page.insert_text((60, 96), "9 «Б» класс, понедельник", fontsize=13, fontname="reg", fontfile=FONT)

    top, row_h, left, width = 130, 52, 60, 722
    cols = (0, 90, 230, 560)
    for i in range(len(LESSONS) + 1):
        y = top + i * row_h
        page.draw_line(fitz.Point(left, y), fitz.Point(left + width, y), width=0.8)
    for c in cols:
        page.draw_line(fitz.Point(left + c, top), fitz.Point(left + c, top + row_h * len(LESSONS)), width=0.8)

    for i, (num, time, subject, room) in enumerate(LESSONS):
        y = top + i * row_h + 33
        for c, text, size in ((12, num, 14), (105, time, 14), (245, subject, 15), (575, room, 13)):
            page.insert_text((left + c, y), text, fontsize=size, fontname="reg", fontfile=FONT)

    doc.save(path)
    doc.close()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="raspisanie-shots-") as tmp:
        src = os.path.join(tmp, "Расписание.pdf")
        sample_pdf(src)

        app = gui.create_app() if hasattr(gui, "create_app") else QApplication(sys.argv)
        win = gui.MainWindow()
        win.resize(1180, 760)
        win.show()

        # Файл читается в рабочем потоке, окно узнаёт об этом по своему таймеру - поэтому
        # ждём не сон, а появление предпросмотра, прокручивая очередь событий.
        win.open_path(src)
        for _ in range(600):
            app.processEvents()
            if getattr(win, "src", None) is not None and not win.loading:
                break
            QTimer.singleShot(0, lambda: None)
            app.thread().msleep(50)
        for _ in range(20):
            app.processEvents()

        shot = OUT / "window.png"
        win.grab().save(str(shot))
        print(f"  {shot.name} ({shot.stat().st_size // 1024} КБ)")
        win.close()
    print(f"Готово: {OUT}")


if __name__ == "__main__":
    main()
