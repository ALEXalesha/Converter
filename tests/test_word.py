import glob
import hashlib
import os
import tempfile
import threading
import winreg

import pytest

import raspisanie_core as core


def word_installed():
    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "Word.Application"))
        return True
    except OSError:
        return False


pytestmark = [pytest.mark.word, pytest.mark.skipif(not word_installed(), reason="нет Microsoft Word")]

win32com = pytest.importorskip("win32com.client")


def new_word():
    word = win32com.DispatchEx("Word.Application")
    word.Visible = False
    word.DisplayAlerts = 0
    return word


def call_with_timeout(fn, seconds=120):
    box = {}

    def target():
        try:
            box["ok"] = fn()
        except Exception as e:
            box["err"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(seconds)
    assert not t.is_alive(), f"зависло дольше {seconds} с"
    if "err" in box:
        raise box["err"]
    return box.get("ok")


@pytest.fixture(scope="module")
def docs(tmp_path_factory):
    folder = tmp_path_factory.mktemp("word")
    plain = str(folder / "Расписание.docx")
    locked = str(folder / "secret.docx")
    word = new_word()
    try:
        for path, pw in ((plain, ""), (locked, "secret")):
            doc = word.Documents.Add()
            doc.PageSetup.Orientation = 1  # landscape
            doc.Content.Text = "WORDMARKER schedule"
            if pw:
                doc.Password = pw  # SaveAs2(Password=...) is silently ignored over late-bound COM
            doc.SaveAs2(path, 16)
            doc.Close(0)
    finally:
        word.Quit(0)
    return plain, locked


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def test_docx_is_converted(docs):
    src = call_with_timeout(lambda: core.load_source(docs[0]))
    assert src.page_count >= 1
    assert src[0].rect.width > src[0].rect.height
    assert "WORDMARKER" in src[0].get_text()


def test_prepare_docx_end_to_end(docs, tmp_path):
    out = str(tmp_path / "out.pdf")
    before = sha(docs[0])
    call_with_timeout(lambda: core.prepare(docs[0], out))
    import pymupdf as fitz
    with fitz.open(out) as doc:
        assert doc.page_count == 1 and "WORDMARKER" in doc[0].get_text()
    assert sha(docs[0]) == before


def test_password_docx_fails_fast(docs):
    with pytest.raises(core.PrintPrepError):
        call_with_timeout(lambda: core.load_source(docs[1]), seconds=90)


def test_no_temp_left(docs):
    pattern = os.path.join(tempfile.gettempdir(), "raspisanie_*")
    before = set(glob.glob(pattern))
    call_with_timeout(lambda: core.load_source(docs[0]))
    with pytest.raises(core.PrintPrepError):
        call_with_timeout(lambda: core.load_source(docs[1]), seconds=90)
    assert set(glob.glob(pattern)) <= before


def test_users_word_stays_open_and_file_open_there(docs):
    user_word = new_word()
    try:
        user_doc = user_word.Documents.Open(docs[0])
        src = call_with_timeout(lambda: core.load_source(docs[0]))
        assert "WORDMARKER" in src[0].get_text()
        assert user_word.Documents.Count == 1
        assert user_doc.Name == os.path.basename(docs[0])
    finally:
        user_word.Quit(0)
