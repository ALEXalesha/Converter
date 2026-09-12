import os
import subprocess
import sys

import pytest

import raspisanie_core as core
from conftest import ROOT

SCRIPT = os.path.join(ROOT, "raspisanie_print.py")


def run(*args):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, env=env,
                          stdin=subprocess.DEVNULL, timeout=60)
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


def test_ok(src_pdf):
    code, out, err = run(src_pdf, "--scale", "0.6", "--page", "2")
    assert code == 0, err
    assert "Готово" in out
    assert os.path.exists(core.default_output_path(src_pdf))


@pytest.mark.parametrize("args", [
    ["--scale", "1.5"], ["--scale", "0"], ["--scale", "nan"], ["--scale", "-1"],
    ["--page", "0"], ["--page", "3"], ["--page", "-1"],
])
def test_bad_options_exit_1_without_traceback(src_pdf, args):
    code, out, err = run(src_pdf, *args)
    assert code == 1
    assert "Ошибка" in err
    assert "Traceback" not in err
    assert not os.path.exists(core.default_output_path(src_pdf))


def test_missing_file(tmp_path):
    code, _, err = run(str(tmp_path / "nope.pdf"))
    assert code == 1 and "не найден" in err


def test_output_same_as_input(src_pdf):
    before = open(src_pdf, "rb").read()
    code, _, err = run(src_pdf, "-o", src_pdf)
    assert code == 1 and "совпадает" in err
    assert open(src_pdf, "rb").read() == before


def test_bad_rotate_is_argparse_error(src_pdf):
    code, _, err = run(src_pdf, "--rotate", "45")
    assert code == 2 and "Traceback" not in err


def test_no_input_without_terminal_does_not_hang():
    code, _, err = run()
    assert code == 2 and "не указан файл" in err


def test_version():
    code, out, _ = run("--version")
    assert code == 0 and out.strip() == core.__version__
