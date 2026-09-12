import hashlib
import math
import os

import pymupdf as fitz
import pytest

import raspisanie_core as core
from conftest import A4_LANDSCAPE, expected_rect, fill_rect, make_src, save_src


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def build(src=None, **kw):
    return core.build_print_doc(src or make_src(), **kw)


def test_output_is_one_portrait_a4_page():
    out = build()
    assert out.page_count == 1
    assert out[0].rect.width == pytest.approx(core.PORTRAIT_W, abs=0.01)
    assert out[0].rect.height == pytest.approx(core.PORTRAIT_H, abs=0.01)


@pytest.mark.parametrize("rotate", core.ROTATIONS)
@pytest.mark.parametrize("scale", [0.3, 0.5, core.DEFAULT_SCALE, 1.0])
def test_content_fills_top_part_and_keeps_proportion(scale, rotate):
    src = make_src()
    got = fill_rect(build(src, scale=scale, rotate=rotate)[0])
    want = expected_rect(src[0], scale, rotate)
    for a, b in zip(got, want):
        assert a == pytest.approx(b, abs=0.5)
    assert got.y1 <= core.PORTRAIT_H * scale + 0.5


@pytest.mark.parametrize("rotate, direction", [(0, (1, 0)), (90, (0, -1)), (270, (0, 1))])
def test_rotation_direction(rotate, direction):
    line = build(rotate=rotate)[0].get_text("dict")["blocks"][0]["lines"][0]
    assert tuple(round(x) for x in line["dir"]) == direction


def test_text_is_kept():
    assert "PAGE1" in build()[0].get_text()


def test_page_choice():
    src = make_src(sizes=(A4_LANDSCAPE, A4_LANDSCAPE, A4_LANDSCAPE))
    assert "PAGE3" in core.build_print_doc(src, page_index=2)[0].get_text()


def test_cut_line_only_when_scaled():
    strokes = [d for d in build(scale=1.0)[0].get_drawings() if d.get("fill") is None]
    assert strokes == []
    strokes = [d for d in build(scale=0.5)[0].get_drawings() if d.get("fill") is None]
    assert len(strokes) == 1
    assert strokes[0]["rect"].y0 == pytest.approx(core.PORTRAIT_H * 0.5, abs=0.5)


@pytest.mark.parametrize("scale", [1.5, 0, -0.5, math.nan, math.inf, -math.inf, True, "0.5", None])
def test_bad_scale_is_rejected(scale):
    with pytest.raises(core.PrintPrepError):
        build(scale=scale)


@pytest.mark.parametrize("page", [-1, 2, 100, 1.0, True, None])
def test_bad_page_is_rejected(page):
    src = make_src(sizes=(A4_LANDSCAPE, A4_LANDSCAPE))
    with pytest.raises(core.PrintPrepError):
        core.build_print_doc(src, page_index=page)


@pytest.mark.parametrize("rotate", [45, 180, -90, 360, None])
def test_bad_rotation_is_rejected(rotate):
    with pytest.raises(core.PrintPrepError):
        build(rotate=rotate)


def test_source_with_own_rotation_still_fits():
    src = make_src(sizes=((595.28, 841.89),), rotation=90)
    got = fill_rect(build(src, scale=0.6, rotate=270)[0])
    want = expected_rect(src[0], 0.6, 270)
    for a, b in zip(got, want):
        assert a == pytest.approx(b, abs=0.5)


def test_blank_page_works():
    src = fitz.open()
    src.new_page(width=842, height=595)
    assert build(src).page_count == 1


def test_output_survives_source_close():
    src = make_src()
    out = core.build_print_doc(src)
    src.close()
    assert core.render_png(out, 200)[:8] == b"\x89PNG\r\n\x1a\n"


def test_prepare_writes_next_to_source(src_pdf):
    out = core.prepare(src_pdf)
    assert out == core.default_output_path(src_pdf)
    assert out.endswith("_печать.pdf")
    with fitz.open(out) as doc:
        assert doc.page_count == 1
        assert "PAGE1" in doc[0].get_text()


def test_prepare_does_not_touch_source(src_pdf):
    before = sha(src_pdf)
    core.prepare(src_pdf, scale=0.5, rotate=90, page_index=1)
    assert sha(src_pdf) == before


def test_prepare_twice_gives_same_picture(src_pdf, tmp_path):
    a = core.prepare(src_pdf, str(tmp_path / "a.pdf"))
    b = core.prepare(src_pdf, str(tmp_path / "b.pdf"))
    with fitz.open(a) as da, fitz.open(b) as db:
        assert da[0].get_pixmap().samples == db[0].get_pixmap().samples


def test_prepare_refuses_to_overwrite_source(src_pdf):
    before = sha(src_pdf)
    with pytest.raises(core.PrintPrepError):
        core.prepare(src_pdf, src_pdf)
    with pytest.raises(core.PrintPrepError):
        core.prepare(src_pdf, src_pdf.upper())
    assert sha(src_pdf) == before


def test_uppercase_extension(tmp_path):
    path = save_src(tmp_path / "SCHEDULE.PDF")
    assert os.path.exists(core.prepare(path))


def test_overwrite_existing_output(src_pdf, tmp_path):
    out = tmp_path / "out.pdf"
    out.write_bytes(b"old")
    core.prepare(src_pdf, str(out))
    assert out.read_bytes().startswith(b"%PDF")


def test_locked_output_gives_clear_error_and_keeps_old_file(src_pdf, tmp_path):
    out = tmp_path / "out.pdf"
    out.write_bytes(b"old")
    with open(out, "rb"):
        with pytest.raises(core.PrintPrepError, match="открыт"):
            core.prepare(src_pdf, str(out))
    assert out.read_bytes() == b"old"
    assert not [n for n in os.listdir(tmp_path) if n.startswith(".tmp_")]


def test_output_is_directory(src_pdf, tmp_path):
    with pytest.raises(core.PrintPrepError, match="папка"):
        core.prepare(src_pdf, str(tmp_path))


def test_output_folder_missing(src_pdf, tmp_path):
    with pytest.raises(core.PrintPrepError, match="Папка"):
        core.prepare(src_pdf, str(tmp_path / "nope" / "out.pdf"))


def test_bad_options_fail_before_writing(src_pdf, tmp_path):
    out = tmp_path / "out.pdf"
    with pytest.raises(core.PrintPrepError):
        core.prepare(src_pdf, str(out), scale=2)
    with pytest.raises(core.PrintPrepError):
        core.prepare(src_pdf, str(out), page_index=5)
    assert not out.exists()


def test_missing_file(tmp_path):
    with pytest.raises(core.PrintPrepError, match="не найден"):
        core.load_source(str(tmp_path / "nope.pdf"))


def test_directory_as_input(tmp_path):
    with pytest.raises(core.PrintPrepError):
        core.load_source(str(tmp_path))


def test_unsupported_extension(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("hi")
    with pytest.raises(core.PrintPrepError, match="Поддерживаются"):
        core.load_source(str(path))


@pytest.mark.parametrize("content", [b"", b"not a pdf at all", b"%PDF-1.7\n garbage"])
def test_broken_pdf(tmp_path, content):
    path = tmp_path / "broken.pdf"
    path.write_bytes(content)
    with pytest.raises(core.PrintPrepError):
        core.load_source(str(path))


def test_encrypted_pdf(tmp_path):
    doc = make_src()
    path = str(tmp_path / "enc.pdf")
    doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o")
    with pytest.raises(core.PrintPrepError, match="парол"):
        core.load_source(path)


def test_source_file_not_locked_after_load(src_pdf):
    src = core.load_source(src_pdf)
    os.remove(src_pdf)
    assert src.page_count == 2


def test_render_png_size():
    out = build()
    png = core.render_png(out, 300)
    pix = fitz.Pixmap(png)
    assert abs(pix.height - 300) <= 1
