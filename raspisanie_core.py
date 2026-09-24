import math
import os
import shutil
import tempfile

import pymupdf as fitz

__version__ = "2.1.0"

PORTRAIT_W = 595.28
PORTRAIT_H = 841.89
DEFAULT_SCALE = 0.78
DEFAULT_ROTATE = 270
ROTATIONS = (0, 90, 270)
WORD_EXTS = (".docx", ".docm", ".doc", ".rtf", ".odt")
SOURCE_EXTS = (".pdf",) + WORD_EXTS
WD_EXPORT_PDF = 17
# A dummy password makes Word fail fast on protected files instead of showing a hidden prompt and hanging
NO_PASSWORD = "#no-password#"


class PrintPrepError(Exception):
    pass


def check_options(scale, rotate):
    if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not math.isfinite(scale) or not 0 < scale <= 1:
        raise PrintPrepError(f"Масштаб должен быть больше 0 и не больше 1, получено: {scale}")
    if rotate not in ROTATIONS:
        raise PrintPrepError(f"Поворот может быть только {', '.join(map(str, ROTATIONS))}, получено: {rotate}")


def check_page(src, page_index):
    if isinstance(page_index, bool) or not isinstance(page_index, int):
        raise PrintPrepError(f"Номер страницы должен быть целым числом: {page_index!r}")
    if not 0 <= page_index < src.page_count:
        raise PrintPrepError(f"Страницы {page_index + 1} нет, в документе {src.page_count} стр.")


def default_output_path(input_path):
    return os.path.splitext(os.path.abspath(input_path))[0] + "_печать.pdf"


def same_path(a, b):
    a, b = os.path.abspath(a), os.path.abspath(b)
    if os.path.exists(a) and os.path.exists(b):
        return os.path.samefile(a, b)
    return os.path.normcase(a) == os.path.normcase(b)


def com_message(err):
    if err.excepinfo and err.excepinfo[2]:
        return err.excepinfo[2].strip()
    return err.strerror or str(err)


def word_to_pdf_bytes(path):
    try:
        import pythoncom
        import win32com.client
    except ImportError as e:
        raise PrintPrepError("Для файлов Word нужен пакет pywin32 (pip install pywin32)") from e

    tmp_dir = tempfile.mkdtemp(prefix="raspisanie_")
    pdf_path = os.path.join(tmp_dir, "source.pdf")
    pythoncom.CoInitialize()
    word = doc = None
    try:
        # DispatchEx starts a separate Word process, so Quit() never closes the user's open documents
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        doc = word.Documents.Open(
            os.path.abspath(path), ConfirmConversions=False, ReadOnly=True,
            AddToRecentFiles=False, PasswordDocument=NO_PASSWORD, Visible=False,
        )
        try:
            doc.ExportAsFixedFormat(pdf_path, WD_EXPORT_PDF)
        finally:
            doc.Close(SaveChanges=0)
        with open(pdf_path, "rb") as f:
            return f.read()
    except pythoncom.com_error as e:
        if word is None:
            raise PrintPrepError("Не удалось запустить Microsoft Word. Он установлен?") from e
        raise PrintPrepError(f"Word не смог открыть или сконвертировать файл: {com_message(e)}") from e
    finally:
        if word is not None:
            try:
                word.Quit(SaveChanges=0)
            except pythoncom.com_error:
                pass
        # Ссылки на Word отпускаем ДО CoUninitialize - такой порядок требует сам COM.
        # Печать «Windows fatal exception: 0x800706BA/0x800706BE» в логе тестов этим не
        # убирается и ошибкой не является: прокси освобождаются уже после Quit(), когда
        # процесса Word нет, RPC гасит это сам, а faulthandler всё равно о них сообщает.
        doc = word = None
        pythoncom.CoUninitialize()
        shutil.rmtree(tmp_dir, ignore_errors=True)


def load_source(path):
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise PrintPrepError(f"Файл не найден: {path}")
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        with open(path, "rb") as f:
            pdf_bytes = f.read()
    elif ext in WORD_EXTS:
        pdf_bytes = word_to_pdf_bytes(path)
    else:
        raise PrintPrepError(f"Поддерживаются только {', '.join(SOURCE_EXTS)}")

    try:
        src = fitz.open(stream=pdf_bytes, filetype="pdf")
    except (fitz.FileDataError, fitz.EmptyFileError) as e:
        raise PrintPrepError(f"Файл повреждён или это не PDF: {os.path.basename(path)}") from e
    if src.needs_pass:
        src.close()
        raise PrintPrepError("PDF защищён паролем, откройте его и сохраните без пароля")
    if src.page_count == 0:
        src.close()
        raise PrintPrepError("В документе нет страниц")
    return src


def build_print_doc(src, scale=DEFAULT_SCALE, rotate=DEFAULT_ROTATE, page_index=0):
    check_options(scale, rotate)
    check_page(src, page_index)

    flat = None
    if src[page_index].rotation:
        # show_pdf_page fits by the rotated size but draws unrotated content, so bake /Rotate into our angle
        flat = fitz.open()
        flat.insert_pdf(src, from_page=page_index, to_page=page_index)
        rotate = (rotate - flat[0].rotation) % 360
        flat[0].set_rotation(0)
        src, page_index = flat, 0

    out = fitz.open()
    page = out.new_page(width=PORTRAIT_W, height=PORTRAIT_H)
    target_h = PORTRAIT_H * scale
    page.show_pdf_page(fitz.Rect(0, 0, PORTRAIT_W, target_h), src, page_index, rotate=rotate)
    if flat is not None:
        flat.close()

    if scale < 0.999:
        page.draw_line(
            fitz.Point(0, target_h), fitz.Point(PORTRAIT_W, target_h),
            color=(0.6, 0.6, 0.6), width=0.5, dashes="[2 2] 0",
        )
    return out


def save_pdf(doc, out_path):
    out_path = os.path.abspath(out_path)
    if os.path.isdir(out_path):
        raise PrintPrepError(f"Это папка, а не файл: {out_path}")
    folder = os.path.dirname(out_path)
    if not os.path.isdir(folder):
        raise PrintPrepError(f"Папка не существует: {folder}")

    pdf_bytes = doc.tobytes(garbage=3, deflate=True)
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".pdf", prefix=".tmp_", dir=folder)
        with os.fdopen(fd, "wb") as f:
            f.write(pdf_bytes)
        os.replace(tmp_path, out_path)
    except PermissionError as e:
        raise PrintPrepError(
            f"Нет доступа к {out_path}. Возможно, файл открыт в другой программе - закройте его и повторите."
        ) from e
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


def check_output(src_path, out_path):
    if same_path(src_path, out_path):
        raise PrintPrepError("Выходной файл совпадает с исходным, выберите другое имя")


def export(src, src_path, out_path, scale=DEFAULT_SCALE, rotate=DEFAULT_ROTATE, page_index=0):
    check_output(src_path, out_path)
    out = build_print_doc(src, scale, rotate, page_index)
    try:
        save_pdf(out, out_path)
    finally:
        out.close()


def prepare(input_path, output_path=None, scale=DEFAULT_SCALE, rotate=DEFAULT_ROTATE, page_index=0):
    check_options(scale, rotate)
    output_path = os.path.abspath(output_path or default_output_path(input_path))
    check_output(input_path, output_path)

    src = load_source(input_path)
    try:
        export(src, input_path, output_path, scale, rotate, page_index)
    finally:
        src.close()
    return output_path


def render_png(doc, height_px):
    page = doc[0]
    zoom = height_px / page.rect.height
    return page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False).tobytes("png")
