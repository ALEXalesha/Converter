import math
import os
import tempfile

import pymupdf as fitz
import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

import raspisanie_core as core
import raspisanie_gui as gui
from conftest import expected_rect, fill_rect, make_src

page_size = st.tuples(st.floats(72, 3000), st.floats(72, 3000))
good_scale = st.floats(0.05, 1.0)
rotation = st.sampled_from(core.ROTATIONS)
EXAMPLES_FACTOR = int(os.environ.get("HYPOTHESIS_FACTOR", "1"))


def many(n):
    return settings(max_examples=n * EXAMPLES_FACTOR, deadline=None, suppress_health_check=[HealthCheck.too_slow])


fast = many(200)


@st.composite
def sources(draw):
    sizes = draw(st.lists(page_size, min_size=1, max_size=4))
    return sizes, draw(st.integers(0, len(sizes) - 1)), draw(st.sampled_from([0, 90, 180, 270]))


def page_contents(doc):
    return [(p.rect, p.rotation, p.read_contents()) for p in doc]


@fast
@given(sources(), good_scale, rotation)
def test_layout_invariants(source, scale, rotate):
    sizes, page_index, own_rotation = source
    src = make_src(sizes, own_rotation)
    before = page_contents(src)

    out = core.build_print_doc(src, scale, rotate, page_index)
    page = out[0]
    assert out.page_count == 1
    assert tuple(page.rect) == pytest.approx((0, 0, core.PORTRAIT_W, core.PORTRAIT_H), abs=0.01)

    got = fill_rect(page)
    want = expected_rect(src[page_index], scale, rotate)
    tol = 0.5
    assert got.x0 >= -tol and got.y0 >= -tol
    assert got.x1 <= core.PORTRAIT_W + tol
    assert got.y1 <= core.PORTRAIT_H * scale + tol
    for a, b in zip(got, want):
        assert a == pytest.approx(b, abs=tol)
    # fit: content touches the page sides or the target height
    assert got.width == pytest.approx(core.PORTRAIT_W, abs=tol) or got.height == pytest.approx(core.PORTRAIT_H * scale, abs=tol)
    assert f"PAGE{page_index + 1}" in page.get_text()
    assert page_contents(src) == before


bad_scale = st.one_of(
    st.floats(max_value=0.0),
    st.floats(min_value=1.0, exclude_min=True),
    st.just(math.nan), st.just(math.inf), st.booleans(), st.text(max_size=3), st.none(),
)


@fast
@given(bad_scale, rotation)
def test_bad_scale_always_clean_error(scale, rotate):
    with pytest.raises(core.PrintPrepError):
        core.build_print_doc(make_src(), scale, rotate, 0)


@fast
@given(st.integers().filter(lambda n: n not in core.ROTATIONS))
def test_bad_rotation_always_clean_error(rotate):
    with pytest.raises(core.PrintPrepError):
        core.build_print_doc(make_src(), core.DEFAULT_SCALE, rotate, 0)


@fast
@given(st.integers(1, 5), st.integers())
def test_page_out_of_range_always_clean_error(n, page_index):
    assume(not 0 <= page_index < n)
    src = make_src(sizes=[(842, 595)] * n)
    with pytest.raises(core.PrintPrepError):
        core.build_print_doc(src, core.DEFAULT_SCALE, core.DEFAULT_ROTATE, page_index)


@many(40)
@given(good_scale, rotation)
def test_build_is_deterministic(scale, rotate):
    src = make_src()
    a = core.build_print_doc(src, scale, rotate, 0)[0].get_pixmap(matrix=fitz.Matrix(0.3, 0.3)).samples
    b = core.build_print_doc(src, scale, rotate, 0)[0].get_pixmap(matrix=fitz.Matrix(0.3, 0.3)).samples
    assert a == b


file_name = st.text(
    alphabet=st.characters(codec="utf-8", categories=["L", "N", "Zs"], include_characters="_-.()"),
    min_size=1, max_size=40,
).map(str.strip).filter(lambda s: s and not s.endswith(".") and s not in (".", ".."))


@many(60)
@given(file_name)
def test_default_output_path_never_overwrites_input(name):
    folder = tempfile.gettempdir()
    for ext in (".pdf", ".docx", ".PDF"):
        src = os.path.join(folder, name + ext)
        out = core.default_output_path(src)
        assert not core.same_path(src, out)
        assert out.endswith("_печать.pdf")
        assert os.path.dirname(out) == os.path.dirname(os.path.abspath(src))


@many(40)
@given(file_name)
def test_save_with_any_unicode_name(name):
    with tempfile.TemporaryDirectory() as d:
        out_path = os.path.join(d, name + ".pdf")
        doc = core.build_print_doc(make_src())
        core.save_pdf(doc, out_path)
        with fitz.open(out_path) as saved:
            assert saved.page_count == 1
        assert os.listdir(d) == [os.path.basename(out_path)]


@many(60)
@given(file_name, st.sampled_from([str.upper, str.lower, str.swapcase]))
def test_same_path_agrees_with_file_system(name, change_case):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, name + ".pdf")
        other = os.path.join(d, change_case(name) + ".pdf")
        guess_before = core.same_path(path, other)
        with open(path, "wb"):
            pass
        truth = os.path.exists(other) and os.path.samefile(path, other)
        assert core.same_path(path, other) == truth
        assert guess_before == truth
        assert core.same_path(path, path)


json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=True) | st.text(max_size=10),
    lambda inner: st.lists(inner, max_size=3) | st.dictionaries(st.text(max_size=5), inner, max_size=3),
    max_leaves=10,
)
settings_dicts = st.fixed_dictionaries(
    {}, optional={"scale": json_values, "rotate": json_values, "open_after": json_values, "last_dir": json_values}
)


@many(300)
@given(st.one_of(json_values, settings_dicts))
def test_clean_settings_always_valid(raw):
    cfg = gui.clean_settings(raw)
    assert isinstance(cfg["scale"], float) and gui.SCALE_MIN / 100 <= cfg["scale"] <= 1
    assert type(cfg["rotate"]) is int and cfg["rotate"] in core.ROTATIONS
    assert isinstance(cfg["open_after"], bool)
    assert cfg["last_dir"] == "" or os.path.isdir(cfg["last_dir"])
    core.check_options(cfg["scale"], cfg["rotate"])
