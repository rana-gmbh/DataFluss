"""Guessing field types, so a new field or a table made from a file needs as few choices as possible.

`from_label` reads a field's name ("E-Mail", "Geburtsdatum", "Preis") in German, English and Chinese.
`column` looks at the values of a column of a file and proposes a type with its settings (decimal places,
unit, options). Both only propose: the person creating the field or table can change every guess.
"""
import re
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from core import fieldtypes as ft
from transfer.parsers import parse_date

# Checked in order; the first match wins. Words are matched at word starts, so "Datum" finds
# "Ausgabedatum" only through its own rule (date words are also matched inside words).
LABEL_RULES = [
    ("text", r"postleitzahl|\bplz\b|\bzip\b|iban|\bbic\b|steuer-?id|kennziffer|邮编|郵編"),  # digits, but not numbers
    ("email", r"e-?mail|mail\b|邮箱|電郵|电子邮件|電子郵件"),
    ("phone", r"telefon|\btel\b|handy|mobil|phone|fax|电话|電話|手机|手機"),
    ("url", r"\burl\b|\blink\b|webseite|website|homepage|internet|网址|網址|链接|連結"),
    ("image", r"\bfoto|\bbild\b|\bphoto|\bimage|\bpicture|照片|图片|圖片"),
    ("file", r"\bdatei|\banhang|\bdokument|\bfile\b|\battachment|\bdocument|\bscan\b|\bpdf\b|文件|附件"),
    ("rating", r"bewertung|sterne\b|\brating\b|\bstars\b|评分|評分"),
    ("datetime", r"uhrzeit|zeitpunkt|\btermin\b|date and time|\bdatetime|时间|時間"),
    ("date", r"datum|geburtstag|geboren|\bdate\b|birthday|\bab\b|\bbis\b|\bseit\b|\bvom\b|\bam$|frist|deadline|日期|生日"),
    ("number", r"anzahl|zahl\b|menge|preis|betrag|kosten|summe|wert\b|gewicht|\bnummer\b|\bnr\b|stück|\balter\b|"
               r"\bjahr\b|\bnote\b|punkte|prozent|count|amount|price|cost|total|quantity|weight|\bage\b|"
               r"数量|价格|價格|金额|金額|数字|數字|年龄|年齡"),
    ("bool", r"^(ist|hat|aktiv|erledigt|bezahlt|vorhanden|abgeschlossen|bestätigt|zurückgegeben)\b|\?$|"
             r"^(is|has|active|done|paid)\b|是否"),
    ("textarea", r"bemerkung|notiz|beschreibung|kommentar|anmerkung|hinweis|\bnotes?\b|description|comment|"
                 r"备注|備註|描述|说明|說明"),
]
_COMPILED = [(kind, re.compile(pattern, re.IGNORECASE)) for kind, pattern in LABEL_RULES]
UNITS = {"€": "€", "eur": "€", "euro": "€", "$": "$", "usd": "$", "£": "£", "%": "%", "kg": "kg", "g": "g",
         "km": "km", "m": "m", "cm": "cm", "l": "l", "h": "h", "min": "min", "stk": "Stk.", "stk.": "Stk.", "¥": "¥", "rmb": "¥", "cny": "¥"}
BOOL_WORDS = {"ja", "nein", "x", "wahr", "falsch", "true", "false", "yes", "no", "j", "n", "y", "✓", "✔", "是", "否"}
_DATETIME = re.compile(r"^\d{1,4}[-./]\d{1,2}[-./]\d{1,4}[ T]\d{1,2}:\d{2}(:\d{2})?")
MAX_OPTIONS = 12


def from_label(label):
    """The type a field name suggests, or "" when it says nothing."""
    text = (label or "").strip()
    for kind, pattern in _COMPILED:
        if pattern.search(text):
            return kind
    return ""


PEOPLE_WORDS = {
    "pupil": r"schüler|kind\b|kinder\b|student|pupil|学生|學生",
    "staff": r"lehrer|lehrkraft|mitarbeit|personal|verantwortlich|betreuer|ansprechpartner(?!.*(mail|tel))|teacher|staff|老师|老師|教师|教師",
    "asset": r"gerät|device|laptop|ipad|tablet|设备|設備",
}


def _fold(text):
    return text.casefold().replace("ä", "a").replace("ö", "o").replace("ü", "u").replace("ß", "ss")


def label_rules(link_targets=(), own=None):
    """The rules for the new-field form, which guesses while the name is typed (static/js/tables.js).
    A name like another table's ("Buch" for "Bücher") or like people and devices proposes a link to it;
    those rules come after numbers and dates, so "Schülerzahl" stays a number."""
    links = []
    for value, label in link_targets:
        if value == f"table:{own}":
            continue
        if value.startswith("table:"):
            stem = _fold(str(label))[:4]
            if len(stem) == 4:
                links.append({"type": "link", "target": value, "pattern": rf"(^|\s){re.escape(stem)}", "fold": True})
        elif value in PEOPLE_WORDS:
            links.append({"type": "link", "target": value, "pattern": PEOPLE_WORDS[value]})
    rules = []
    for kind, pattern in LABEL_RULES:
        rules.append({"type": kind, "pattern": pattern})
        if kind == "number":
            rules.extend(links)
    return rules


def _split_unit(text):
    """("12,50", "€") from "12,50 €" or "€ 12,50"; the unit only when it is a known one."""
    match = re.fullmatch(r"\s*([^\d\s+-]{1,4})?\s*([-+]?[\d.,  ]+?)\s*([^\d\s]{1,4})?\s*", text)
    if not match:
        return text, ""
    before, number, after = match.groups()
    unit = (before or after or "").strip()
    if before and after:
        return text, ""
    if unit and unit.lower() not in UNITS:
        return text, ""
    return number, UNITS.get(unit.lower(), "") if unit else ""


def _number(text):
    """(Decimal, decimal places) for a number as written in a file (read like the import reads it,
    core/fieldtypes.py normalize_number), else None. Leading zeros (postcodes, register numbers) are text."""
    if not re.fullmatch(r"[-+]?[\d.,  ']*\d[\d.,  ']*", text.strip()):
        return None
    plain = ft.normalize_number(text.strip())
    if re.match(r"[-+]?0\d", plain):
        return None
    try:
        value = Decimal(plain)
    except InvalidOperation:
        return None
    return value, len(plain.split(".", 1)[1]) if "." in plain else 0


def _is_email(text):
    try:
        validate_email(text.removeprefix("mailto:"))
        return True
    except ValidationError:
        return False


def column(header, values):
    """{"type", "config", "options"} for a column with these cell texts (empty cells are ignored)."""
    seen = [v.strip() for v in values if v and v.strip()]
    hint = from_label(header)
    if not seen:
        return {"type": hint if hint in ("text", "textarea", "number", "rating", "date", "datetime", "bool", "email", "phone", "url") else "text",
                "config": {}, "options": []}
    lowered = {v.lower() for v in seen}
    if lowered <= BOOL_WORDS and not lowered <= {"1", "0"}:
        return {"type": "bool", "config": {}, "options": []}
    if all(_is_email(v) for v in seen):
        return {"type": "email", "config": {}, "options": []}
    if all(re.match(r"^(https?://|www\.)\S+$", v, re.IGNORECASE) for v in seen):
        return {"type": "url", "config": {}, "options": []}
    if all(_DATETIME.match(v) for v in seen) and _all_parse(seen, ft.parse_datetime):
        return {"type": "datetime", "config": {}, "options": []}
    if _all_parse(seen, parse_date) and not all(v.isdigit() for v in seen):
        return {"type": "date", "config": {}, "options": []}
    if hint == "phone" or all(re.fullmatch(r"\+?[\d ()/-]{6,}", v) and re.search(r"[ /()-]|^\+", v) for v in seen):
        return {"type": "phone", "config": {}, "options": []}
    numbers, units = [], set()
    for v in seen:
        text, unit = _split_unit(v)
        parsed = _number(text)
        if parsed is None:
            break
        numbers.append(parsed)
        units.add(unit)
    else:
        if len(units) == 1:
            places = min(max(n[1] for n in numbers), 6)
            config = {"decimals": places} if places else {}
            if unit := units.pop():
                config["unit"] = unit
                if unit in ("€", "$", "£", "¥"):
                    config["decimals"] = 2
            return {"type": "number", "config": config, "options": []}
    if any("\n" in v or len(v) > 120 for v in seen):
        return {"type": "textarea", "config": {}, "options": []}
    distinct = list(dict.fromkeys(seen))
    if len(distinct) <= MAX_OPTIONS and len(seen) >= 2 * len(distinct) and max(len(v) for v in distinct) <= 40:
        return {"type": "choice", "config": {}, "options": distinct}
    if hint in ("email", "url", "textarea"):
        return {"type": hint, "config": {}, "options": []}
    return {"type": "text", "config": {}, "options": []}


def _all_parse(values, parse):
    for v in values:
        try:
            if parse(v) is None:
                return False
        except (ValueError, TypeError, ValidationError):
            return False
    return True


IDENTIFIER = re.compile(r"nummer|\bnr\b|\bid\b|kennung|isbn|serien|inventar|kürzel|code|e-?mail|number|serial|编号|編號",
                        re.IGNORECASE)


def is_identifier(header, values):
    """A column that names each row once, like a serial or inventory number: unique in the new table."""
    return bool(IDENTIFIER.search(header or "")) and is_unique(values)


def is_unique(values):
    seen = [v.strip() for v in values if v and v.strip()]
    return bool(seen) and len(seen) == len(set(v.casefold() for v in seen)) and len(seen) == len([v for v in values])
