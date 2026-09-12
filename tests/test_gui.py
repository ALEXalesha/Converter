import json
import os
import time
import tkinter as tk

import pytest

import raspisanie_core as core
import raspisanie_gui as gui


class Calls(list):
    pass


@pytest.fixture
def dialogs(monkeypatch):
    calls = Calls()
    for name in ("showerror", "showinfo", "showwarning"):
        monkeypatch.setattr(gui.messagebox, name, lambda *a, _n=name, **k: calls.append((_n, a)))
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **k: True)
    opened = []
    monkeypatch.setattr(gui.os, "startfile", lambda p: opened.append(p), raising=False)
    calls.opened = opened
    return calls


# Creating many tk.Tk() roots in one process makes Tcl init fail at random, so tests share one root
@pytest.fixture(scope="session")
def tk_root():
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def window(tk_root):
    windows = []

    def make():
        win = tk.Toplevel(tk_root)
        windows.append(win)
        return win

    yield make
    for win in windows:
        if win.winfo_exists():
            win.destroy()


@pytest.fixture
def app(tmp_path, monkeypatch, dialogs, window):
    monkeypatch.setattr(gui, "SETTINGS_PATH", str(tmp_path / "cfg" / "settings.json"))
    win = window()
    a = gui.App(win)
    win.geometry("980x700+0+0")
    win.update()
    return a


def pump(app, done, timeout=15):
    end = time.time() + timeout
    while not done() and time.time() < end:
        app.root.update()
        time.sleep(0.02)
    app.root.update()
    assert done(), "не дождались"


def open_file(app, path):
    app.open_path(path)
    pump(app, lambda: not app.loading)


def test_open_pdf_shows_preview(app, src_pdf):
    open_file(app, src_pdf)
    assert app.src is not None and app.src.page_count == 2
    assert app.out_var.get() == core.default_output_path(src_pdf)
    assert app.pages_label.cget("text") == "из 2"
    assert app.preview_img is not None and app.preview_img.height() > 100
    assert app.path_entry.xview()[1] == 1.0 and app.out_entry.xview()[1] == 1.0


def test_preview_follows_options(app, src_pdf):
    open_file(app, src_pdf)
    for scale in (30, 55, 100):
        for rotate in core.ROTATIONS:
            app.scale_var.set(scale)
            app.rotate_var.set(rotate)
            app.update_preview()
            assert app.status_label.cget("style") != "Error.TLabel"


def test_bad_page_text_shows_error_not_crash(app, src_pdf, dialogs):
    open_file(app, src_pdf)
    for text in ("abc", "0", "3", "-1", ""):
        app.page_spin.set(text)
        app.update_preview()
        assert app.status_label.cget("style") == "Error.TLabel"
        app.save()
        assert dialogs[-1][0] == "showerror"
    app.page_spin.set("2")
    app.update_preview()
    assert app.status_label.cget("style") != "Error.TLabel"


def test_save_writes_pdf_and_settings(app, src_pdf, dialogs):
    open_file(app, src_pdf)
    app.scale_var.set(50)
    app.rotate_var.set(90)
    app.open_after_var.set(True)
    app.save()
    out = core.default_output_path(src_pdf)
    assert os.path.exists(out)
    assert dialogs.opened == [out]
    with open(gui.SETTINGS_PATH, encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["scale"] == 0.5 and saved["rotate"] == 90


def test_save_without_opening(app, src_pdf, dialogs):
    open_file(app, src_pdf)
    app.open_after_var.set(False)
    app.save()
    assert dialogs.opened == []


def test_save_over_source_is_refused(app, src_pdf, dialogs):
    before = open(src_pdf, "rb").read()
    open_file(app, src_pdf)
    app.out_var.set(src_pdf)
    app.save()
    assert dialogs[-1][0] == "showerror"
    assert open(src_pdf, "rb").read() == before


def test_save_before_open(app, dialogs):
    app.save()
    assert dialogs[-1][0] == "showinfo"


def test_broken_file_keeps_app_usable(app, tmp_path, src_pdf, dialogs):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"nope")
    open_file(app, str(bad))
    assert dialogs[-1][0] == "showerror"
    assert app.src is None and not app.loading
    open_file(app, src_pdf)
    assert app.src is not None


def test_settings_restored_on_next_start(app, window):
    app.scale_var.set(64)
    app.rotate_var.set(0)
    app.close()
    assert not app.root.winfo_exists()
    again = gui.App(window())
    assert again.scale_var.get() == 64
    assert again.rotate_var.get() == 0


@pytest.mark.parametrize("content", [
    '{"scale": "abc", "rotate": false, "last_dir": 5',
    '{"scale": NaN, "rotate": true, "open_after": "yes"}',
    '[1, 2, 3]',
    '',
])
def test_corrupt_settings_file(tmp_path, monkeypatch, dialogs, window, content):
    path = tmp_path / "settings.json"
    path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(gui, "SETTINGS_PATH", str(path))
    a = gui.App(window())
    assert a.scale_var.get() == round(core.DEFAULT_SCALE * 100)
    assert a.rotate_var.get() == core.DEFAULT_ROTATE


def test_initial_path_from_command_line(tmp_path, monkeypatch, src_pdf, dialogs, window):
    monkeypatch.setattr(gui, "SETTINGS_PATH", str(tmp_path / "s.json"))
    a = gui.App(window(), src_pdf)
    pump(a, lambda: a.src is not None)


def test_close_while_loading_asks(app, dialogs, monkeypatch):
    app.loading = True
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **k: False)
    app.close()
    assert app.root.winfo_exists()
    app.loading = False
