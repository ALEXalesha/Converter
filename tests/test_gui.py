"""Окно на Qt: загрузка, предпросмотр, настройки, сохранение (окна на экране не появляются)."""
import json
import os
import random
import time

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import raspisanie_core as core
import raspisanie_gui as gui

from conftest import save_src


class Calls(list):
    pass


@pytest.fixture
def dialogs(monkeypatch):
    calls = Calls()
    for name in ("info", "warning", "error"):
        monkeypatch.setattr(gui.dialogs, name, staticmethod(lambda _p, t, _n=name: calls.append((_n, t))))
    calls.answer = True
    monkeypatch.setattr(gui.dialogs, "yes_no", staticmethod(lambda _p, t: calls.append(("yes_no", t)) or calls.answer))
    calls.opened = []
    monkeypatch.setattr(gui.dialogs, "open_file", staticmethod(calls.opened.append))
    calls.open_paths, calls.save_paths = [], []
    monkeypatch.setattr(gui.dialogs, "open_path", staticmethod(lambda _p, _d: calls.open_paths.pop(0)))
    monkeypatch.setattr(gui.dialogs, "save_path", staticmethod(lambda _p, _s: calls.save_paths.pop(0)))
    return calls


@pytest.fixture
def make_app(qapp, tmp_path, monkeypatch, dialogs):
    monkeypatch.setattr(gui, "SETTINGS_PATH", str(tmp_path / "cfg" / "settings.json"))
    made = []

    def make(initial=None):
        w = gui.MainWindow(initial)
        w.setAttribute(Qt.WA_DontShowOnScreen)
        w.show()
        QApplication.processEvents()
        made.append(w)
        return w
    yield make
    for w in made:
        if not w.closed:
            w.loading = False
            w.close()
        w.deleteLater()


@pytest.fixture
def app(make_app):
    return make_app()


def pump(app, done, timeout=15):
    end = time.time() + timeout
    while not done() and time.time() < end:
        QApplication.processEvents()
        app._poll_jobs()
        time.sleep(0.02)
    QApplication.processEvents()
    assert done(), "не дождались"


def open_file(app, path):
    app.open_path(path)
    pump(app, lambda: not app.loading)


def assert_consistent(app):
    """Инварианты окна после любого шага."""
    assert app.open_btn.isEnabled() == app.save_btn.isEnabled() == (not app.loading)
    assert app.progress.isVisible() == app.loading
    assert ("color" in app.status_label.styleSheet()) == app.error
    if app.src is not None:
        assert app.page_spin.minimum() == 1 and app.page_spin.maximum() == app.src.page_count
        assert 1 <= app.page_spin.value() <= app.src.page_count
        assert app.pages_label.text() == f"из {app.src.page_count}"
    assert gui.SCALE_MIN <= app.scale_slider.value() <= 100
    assert app.rotate in core.ROTATIONS
    assert app.scale_label.text() == f"{app.scale_slider.value()} %"
    if os.path.exists(gui.SETTINGS_PATH):
        with open(gui.SETTINGS_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        assert gui.clean_settings(raw) == raw, "на диске только чистые настройки"


def test_empty_window(app):
    assert app.src is None and app.preview.image is None
    assert "перетащите" in app.status_label.text()
    img = app.preview.grab()
    assert not img.isNull()
    assert_consistent(app)


def test_open_pdf_shows_preview(app, src_pdf):
    open_file(app, src_pdf)
    assert app.src is not None and app.src.page_count == 2
    assert app.out_edit.text() == core.default_output_path(src_pdf)
    assert app.pages_label.text() == "из 2"
    assert app.preview.image is not None and app.preview.image.height() > 100
    assert app.path_edit.cursorPosition() == len(app.path_edit.text())
    assert_consistent(app)


def test_busy_state_while_loading(app, src_pdf, dialogs):
    app.open_path(src_pdf)
    assert app.loading and not app.open_btn.isEnabled() and not app.save_btn.isEnabled()
    assert app.progress.isVisible()
    app.open_path(src_pdf)  # второй запуск во время загрузки игнорируется
    app.choose_file()       # и выбор файла тоже (диалог не открывается)
    app.save()              # и сохранение
    assert list(dialogs) == [], "во время загрузки никаких сообщений"
    pump(app, lambda: not app.loading)
    assert_consistent(app)


def test_ctrl_s_while_loading_does_not_save(app, src_pdf, tmp_path, dialogs):
    open_file(app, src_pdf)
    out = tmp_path / "busy.pdf"
    app.out_edit.setText(str(out))
    app.open_path(src_pdf)
    app.save_shortcut.activated.emit()  # кнопка выключена, но сочетание всё равно приходит
    assert not out.exists() and list(dialogs) == []
    pump(app, lambda: not app.loading)


def _track_close(monkeypatch, closed):
    real = core.load_source

    def load(path):
        src = real(path)
        orig = src.close
        src.close = lambda: (closed.append(src), orig())[1]
        return src
    monkeypatch.setattr(core, "load_source", load)


def test_reopen_closes_previous_source(app, src_pdf, tmp_path, monkeypatch):
    closed = []
    _track_close(monkeypatch, closed)
    open_file(app, src_pdf)
    first = app.src
    open_file(app, save_src(tmp_path / "second.pdf"))
    assert closed == [first], "старый документ закрыт, новый открыт"
    app.close()
    assert len(closed) == 2 and app.src is None


def test_load_finishing_after_close_releases_file(app, src_pdf, monkeypatch):
    closed = []
    _track_close(monkeypatch, closed)
    app.open_path(src_pdf)
    app.close()
    pump(app, lambda: bool(closed))
    assert app.src is None and len(closed) == 1, "файл, догрузившийся после закрытия, не остаётся открытым"


def test_preview_image_matches_a4_and_fits(app, src_pdf):
    open_file(app, src_pdf)
    for size in ((980, 700), (1400, 900), (820, 600)):
        app.resize(*size)
        QApplication.processEvents()
        app.update_preview()
        r = app.preview.page_rect()
        assert r.left() >= 0 and r.top() >= 0
        assert r.right() <= app.preview.width() and r.bottom() <= app.preview.height()
        assert abs(r.width() / r.height() - core.PORTRAIT_W / core.PORTRAIT_H) < 0.01
        img = app.preview.image
        assert abs(img.width() / img.height() - core.PORTRAIT_W / core.PORTRAIT_H) < 0.02


def test_resize_keeps_old_image_until_rerender(app, src_pdf):
    open_file(app, src_pdf)
    old = app.preview.image
    app.resize(1200, 850)
    QApplication.processEvents()
    assert app.preview.image is old, "во время ресайза рисуется готовая картинка"
    assert app.preview_timer.isActive()
    QTest.qWait(gui.PREVIEW_DELAY_MS + 80)
    assert app.preview.image is not old


def test_preview_follows_options(app, src_pdf):
    open_file(app, src_pdf)
    seen = set()
    for scale in (30, 55, 100):
        for rotate in core.ROTATIONS:
            app.scale_slider.setValue(scale)
            app.set_rotate(rotate)
            app.update_preview()
            assert not app.error
            img = app.preview.image
            seen.add(bytes(img.constBits())[:4096] if img.constBits() is not None else id(img))
    assert len(seen) > 1, "разные настройки дают разные картинки"


def test_page_spin_cannot_leave_range(app, src_pdf):
    open_file(app, src_pdf)
    for value in (0, -1, 3, 999):
        app.page_spin.setValue(value)
        assert 1 <= app.page_spin.value() <= 2
    app.page_spin.setValue(2)
    app.update_preview()
    assert not app.error
    assert_consistent(app)


def test_error_status_clears_after_fix(app, src_pdf, monkeypatch):
    open_file(app, src_pdf)
    real = core.build_print_doc

    def broken(*a, **k):
        raise core.PrintPrepError("сломано")
    monkeypatch.setattr(core, "build_print_doc", broken)
    app.update_preview()
    assert app.error and "сломано" in app.status_label.text()
    assert_consistent(app)
    monkeypatch.setattr(core, "build_print_doc", real)
    app.update_preview()
    assert not app.error
    assert_consistent(app)


def test_save_writes_pdf_and_settings(app, src_pdf, dialogs):
    open_file(app, src_pdf)
    app.scale_slider.setValue(50)
    app.set_rotate(90)
    app.open_after.setChecked(True)
    app.save()
    out = core.default_output_path(src_pdf)
    assert os.path.exists(out)
    assert dialogs.opened == [out]
    with open(gui.SETTINGS_PATH, encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["scale"] == 0.5 and saved["rotate"] == 90
    assert_consistent(app)


def test_save_without_opening(app, src_pdf, dialogs):
    open_file(app, src_pdf)
    app.open_after.setChecked(False)
    app.save()
    assert dialogs.opened == []


def test_open_failure_after_save_warns(app, src_pdf, dialogs, monkeypatch):
    open_file(app, src_pdf)

    def fail(_p):
        raise OSError("нет программы для PDF")
    monkeypatch.setattr(gui.dialogs, "open_file", staticmethod(fail))
    app.save()
    assert dialogs[-1][0] == "warning" and os.path.exists(core.default_output_path(src_pdf))


def test_save_over_source_is_refused(app, src_pdf, dialogs):
    before = open(src_pdf, "rb").read()
    open_file(app, src_pdf)
    app.out_edit.setText(src_pdf)
    app.save()
    assert dialogs[-1][0] == "error" and app.error
    assert open(src_pdf, "rb").read() == before


def test_save_before_open_and_empty_target(app, src_pdf, dialogs):
    app.save()
    assert dialogs[-1][0] == "info"
    open_file(app, src_pdf)
    app.out_edit.setText("   ")
    app.save()
    assert dialogs[-1] == ("error", "Укажите, куда сохранить PDF.")


def test_choose_output_adds_pdf_extension(app, src_pdf, dialogs, tmp_path):
    open_file(app, src_pdf)
    dialogs.save_paths.append(str(tmp_path / "out"))
    app.choose_output()
    assert app.out_edit.text() == os.path.normpath(str(tmp_path / "out.pdf"))
    dialogs.save_paths.append("")  # закрыли окно выбора
    app.choose_output()
    assert app.out_edit.text() == os.path.normpath(str(tmp_path / "out.pdf"))


def test_choose_file_uses_dialog(app, src_pdf, dialogs):
    dialogs.open_paths.append(src_pdf)
    app.choose_file()
    pump(app, lambda: not app.loading)
    assert app.src is not None
    dialogs.open_paths.append("")
    app.choose_file()
    assert app.src_path == os.path.abspath(src_pdf)


def test_broken_file_keeps_app_usable(app, tmp_path, src_pdf, dialogs):
    open_file(app, src_pdf)
    first = app.src
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"nope")
    open_file(app, str(bad))
    assert dialogs[-1][0] == "error" and app.error
    assert app.src is first and not app.loading, "старый файл остаётся открытым"
    assert_consistent(app)
    other = save_src(tmp_path / "other.pdf")
    open_file(app, other)
    assert app.src_path == os.path.abspath(other) and not app.error


def test_settings_restored_on_next_start(make_app):
    a = make_app()
    a.scale_slider.setValue(64)
    a.set_rotate(0)
    a.open_after.setChecked(False)
    a.close()
    assert a.closed
    b = make_app()
    assert b.scale_slider.value() == 64 and b.rotate == 0 and not b.open_after.isChecked()


def test_reset_options(app):
    app.scale_slider.setValue(40)
    app.set_rotate(0)
    app.reset_options()
    assert app.scale_slider.value() == round(core.DEFAULT_SCALE * 100)
    assert app.rotate == core.DEFAULT_ROTATE


@pytest.mark.parametrize("content", [
    '{"scale": "abc", "rotate": false, "last_dir": 5',
    '{"scale": NaN, "rotate": true, "open_after": "yes"}',
    '[1, 2, 3]',
    '',
    '{"scale": 0.1, "rotate": 45}',
])
def test_corrupt_settings_file(tmp_path, monkeypatch, make_app, content):
    path = tmp_path / "cfg" / "settings.json"
    path.parent.mkdir()
    path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(gui, "SETTINGS_PATH", str(path))
    a = make_app()
    assert a.scale_slider.value() == round(core.DEFAULT_SCALE * 100)
    assert a.rotate == core.DEFAULT_ROTATE


def test_initial_path_from_command_line(make_app, src_pdf):
    a = make_app(src_pdf)
    pump(a, lambda: a.src is not None)


def test_close_while_loading_asks(app, dialogs, src_pdf):
    app.open_path(src_pdf)
    dialogs.answer = False
    app.close()
    assert not app.closed and dialogs[-1][0] == "yes_no"
    dialogs.answer = True
    app.close()
    assert app.closed
    pump(app, lambda: not app.loading)  # загрузка, закончившаяся после закрытия, ничего не ломает
    assert app.src is None


def test_shortcuts(app, dialogs, src_pdf):
    # Сочетания Qt срабатывают в активном окне; тестовое окно не активно, поэтому зовём их напрямую.
    assert app.open_shortcut.key().toString() == "Ctrl+O"
    assert app.save_shortcut.key().toString() == "Ctrl+S"
    dialogs.open_paths.append(src_pdf)
    app.open_shortcut.activated.emit()
    pump(app, lambda: app.src is not None)
    app.open_after.setChecked(False)
    app.save_shortcut.activated.emit()
    assert os.path.exists(core.default_output_path(src_pdf))


def _drop(app, paths):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
    enter = QDragEnterEvent(app.rect().center(), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(app, enter)
    accepted = enter.isAccepted()
    if accepted:
        drop = QDropEvent(QPointF(app.rect().center()), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(app, drop)
    return accepted


def test_drag_and_drop_opens_schedule(app, src_pdf, tmp_path):
    assert _drop(app, [src_pdf])
    pump(app, lambda: app.src is not None)
    txt = tmp_path / "x.txt"
    txt.write_text("x")
    assert not _drop(app, [str(txt)]), "не расписание — не принимаем"
    assert not _drop(app, [src_pdf, src_pdf]), "несколько файлов — не принимаем"


def test_drop_ignored_while_loading(app, src_pdf, tmp_path):
    other = save_src(tmp_path / "other.pdf")
    app.open_path(src_pdf)
    assert not _drop(app, [other])
    # Бросок без входа (Qt так не делает, но окно не должно на это полагаться).
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(other)])
    drop = QDropEvent(QPointF(app.rect().center()), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(app, drop)
    pump(app, lambda: not app.loading)
    assert app.src_path == os.path.abspath(src_pdf)


def test_random_sequences(app, src_pdf, dialogs, tmp_path):
    open_file(app, src_pdf)
    rng = random.Random(5)
    for step in range(120):
        op = rng.choice(["page", "rotate", "scale", "resize", "preview", "save", "reset", "reopen"])
        if op == "page":
            app.page_spin.setValue(rng.randint(-2, 5))
        elif op == "rotate":
            app.set_rotate(rng.choice(core.ROTATIONS))
        elif op == "scale":
            app.scale_slider.setValue(rng.randint(0, 120))
        elif op == "resize":
            app.resize(rng.randint(820, 1500), rng.randint(600, 1000))
            QApplication.processEvents()
        elif op == "preview":
            app.update_preview()
        elif op == "save":
            app.open_after.setChecked(False)
            app.out_edit.setText(str(tmp_path / f"out{step % 3}.pdf"))
            app.save()
        elif op == "reset":
            app.reset_options()
        elif op == "reopen":
            open_file(app, src_pdf)
        assert_consistent(app)
        assert not app.error, (step, op, app.status_label.text())
