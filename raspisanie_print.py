#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Готовит расписание (.docx или .pdf, альбомная A4) к печати на книжном листе A4:
поворачивает страницу на 90 градусов и вписывает её в верхнюю часть листа.

Использование:
    python raspisanie_print.py "Расписание.docx"
    python raspisanie_print.py "Расписание.docx" -o "печать.pdf"
    python raspisanie_print.py "Расписание.docx" --scale 0.9 --page 2

Окно с предпросмотром: python raspisanie_gui.py
"""

import argparse
import os
import sys

import raspisanie_core as core

DEFAULT_INPUT = r"C:\Drive\Alexey\Word txt\Расписание.docx"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Готовит расписание к печати: поворот + масштаб на книжный A4.")
    parser.add_argument("input", nargs="?", help="Путь к .docx, .doc, .rtf, .odt или .pdf файлу расписания")
    parser.add_argument("-o", "--output", help="Путь к выходному PDF (по умолчанию рядом с исходным файлом)")
    parser.add_argument("--scale", type=float, default=core.DEFAULT_SCALE,
                        help=f"Доля высоты книжного листа под таблицу (0-1), по умолчанию {core.DEFAULT_SCALE}")
    parser.add_argument("--rotate", type=int, default=core.DEFAULT_ROTATE, choices=core.ROTATIONS,
                        help="270 = по часовой (по умолчанию), 90 = против часовой, 0 = без поворота")
    parser.add_argument("--page", type=int, default=1, help="Номер страницы для печати (с 1), по умолчанию первая")
    parser.add_argument("--version", action="version", version=core.__version__)
    args = parser.parse_args(argv)

    input_path = args.input
    if not input_path:
        print(f"Файл не указан. Enter - взять {DEFAULT_INPUT}")
        try:
            answer = input("Путь к файлу (можно перетащить сюда): ").strip().strip('"')
        except (EOFError, RuntimeError):
            parser.error("не указан файл")
        input_path = answer or DEFAULT_INPUT

    if os.path.splitext(input_path)[1].lower() in core.WORD_EXTS:
        print("Конвертирую в PDF через Word...")
    try:
        out_path = core.prepare(input_path, args.output, args.scale, args.rotate, args.page - 1)
    except core.PrintPrepError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return 1

    print(f"Готово: {out_path}")
    print("Печатать в настройках принтера как «Реальный размер / Без масштабирования».")
    return 0


if __name__ == "__main__":
    sys.exit(main())
