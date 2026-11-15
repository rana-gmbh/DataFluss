import csv
import io
import re

from django.utils.translation import gettext as _
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

PHONE = re.compile(r"^\+?[0-9 ()./-]+$")


def _safe(value):
    """Stops spreadsheet formulas being injected through data, without touching phone numbers."""
    if isinstance(value, bool):
        return _("Ja") if value else _("Nein")
    text = "" if value is None else str(value)
    if text and text[0] in "=+-@\t\r" and not PHONE.match(text):
        return "'" + text
    return text


def to_csv(titles, rows):
    buffer = io.StringIO()
    buffer.write("﻿")
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(titles)
    for row in rows:
        writer.writerow([_safe(v) for v in row])
    return buffer.getvalue().encode("utf-8")


def to_xlsx(titles, rows, sheet=None):
    book = Workbook()
    ws = book.active
    ws.title = (sheet or _("Daten"))[:31]
    ws.append(titles)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2553EB")
    for row in rows:
        ws.append([_safe(v) for v in row])
    for column in ws.columns:
        letter = get_column_letter(column[0].column)
        for cell in column[1:]:
            cell.number_format = "@"
        ws.column_dimensions[letter].width = min(max(len(str(c.value or "")) for c in list(column)[:200]) + 2, 42)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()
