import os
import sys

import pymupdf as fitz
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

A4_LANDSCAPE = (841.89, 595.28)
FILL = (1.0, 0.8, 0.2)


def make_src(sizes=(A4_LANDSCAPE,), rotation=0):
    doc = fitz.open()
    for i, (w, h) in enumerate(sizes):
        page = doc.new_page(width=w, height=h)
        page.draw_rect(page.rect, color=None, fill=FILL)
        page.insert_text((min(20, w / 4), min(40, h / 2)), f"PAGE{i + 1}", fontsize=min(12, h / 4))
        if rotation:
            page.set_rotation(rotation)
    return doc


def save_src(path, sizes=(A4_LANDSCAPE,), rotation=0):
    doc = make_src(sizes, rotation)
    doc.save(str(path))
    doc.close()
    return str(path)


def fill_rect(page):
    rects = [d["rect"] for d in page.get_drawings() if d.get("fill") is not None]
    assert rects, "на выходной странице нет залитой области"
    r = fitz.Rect(rects[0])
    for x in rects[1:]:
        r |= x
    return r


def expected_rect(src_page, scale, rotate):
    cw, ch = src_page.rect.width, src_page.rect.height
    if rotate in (90, 270):
        cw, ch = ch, cw
    tw, th = 595.28, 841.89 * scale
    k = min(tw / cw, th / ch)
    w, h = cw * k, ch * k
    return fitz.Rect((tw - w) / 2, (th - h) / 2, (tw + w) / 2, (th + h) / 2)


@pytest.fixture
def src_pdf(tmp_path):
    return save_src(tmp_path / "Расписание.pdf", sizes=(A4_LANDSCAPE, A4_LANDSCAPE))
