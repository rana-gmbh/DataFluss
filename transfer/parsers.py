import csv
import io
import itertools
import re
import zipfile
from datetime import date, datetime

from django.utils.translation import gettext as _
from openpyxl import load_workbook

MAX_ROWS = 50_000
MAX_UNPACKED_BYTES = 200 * 1024 * 1024

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "mär": 3, "maer": 3, "apr": 4, "may": 5, "mai": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "okt": 10, "nov": 11, "dec": 12, "dez": 12,
}


def _decode(raw):
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _delimiter(sample):
    counts = {d: sample.count(d) for d in ";,\t"}
    return max(counts, key=counts.get)


def read_table(name, raw):
    """Returns (headers, rows). Every value stays text, so leading zeros survive."""
    if name.lower().endswith((".xlsx", ".xlsm")):
        return _read_xlsx(raw)
    text = _decode(raw)
    first_line = text.split("\n", 1)[0]
    reader = csv.reader(io.StringIO(text), delimiter=_delimiter(first_line))
    table = [row for row in reader if any(cell.strip() for cell in row)]
    if not table:
        return [], []
    headers = [h.strip() for h in table[0]]
    rows = [[(row[i].strip() if i < len(row) else "") for i in range(len(headers))] for row in table[1:]]
    return headers, rows


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _read_xlsx(raw):
    """A small file can unpack to gigabytes, so the content is measured first."""
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if sum(item.file_size for item in archive.infolist()) > MAX_UNPACKED_BYTES:
            raise ValueError("Die Datei ist ausgepackt zu groß.")
    book = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    sheet = book.worksheets[0]
    table = [[_cell(c) for c in row] for row in itertools.islice(sheet.iter_rows(values_only=True), MAX_ROWS)]
    table = [row for row in table if any(row)]
    if not table:
        return [], []
    headers = [h for h in table[0]]
    while headers and not headers[-1]:
        headers.pop()
    return headers, [row[: len(headers)] + [""] * (len(headers) - len(row)) for row in table[1:]]


def parse_date(text):
    """Accepts ISO, German dates, and Ninox's '27. May 2018' with English months."""
    text = (text or "").strip()
    if not text:
        return None
    match = re.fullmatch(r"(\d{1,2})\.\s*([A-Za-zäÄ]+)\.?\s*(\d{4})", text)
    if match:
        month = MONTHS.get(match.group(2)[:3].lower())
        if month:
            try:
                return date(int(match.group(3)), month, int(match.group(1)))
            except ValueError:
                raise ValueError(_("Datum nicht lesbar: %(value)s") % {"value": text})
    for pattern in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            pass
    raise ValueError(_("Datum nicht lesbar: %(value)s") % {"value": text})


def parse_bool(text):
    return (text or "").strip().lower() in ("yes", "ja", "1", "true", "x", "wahr")
