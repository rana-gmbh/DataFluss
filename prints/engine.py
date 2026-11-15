"""Turns a template and records into a PDF.

Rendered PDFs stay in memory and are returned to the browser, never written to disk.
"""
import base64
import html
import io
import mimetypes
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

import segno
from django.conf import settings
from django.utils import timezone
from jinja2 import select_autoescape
from jinja2.sandbox import ImmutableSandboxedEnvironment
from markupsafe import Markup
from weasyprint import HTML
from weasyprint.text.fonts import FontConfiguration
from weasyprint.urls import URLFetcher, URLFetchingError

from core.models import SchoolSettings

from .presets import PRESETS

FONT_DIR = Path(__file__).resolve().parent / "fonts"
BASE_FONT = "Inter, 'Noto Sans SC', sans-serif"
MONO_FONT = "'JetBrains Mono', 'Noto Sans SC', monospace"
DEFAULT_PAGE = {"preset": "a4", "margin": [12, 18, 16, 18], "footer": True}


class SafeFetcher(URLFetcher):
    """Templates can reach embedded data and bundled fonts, nothing else."""

    def fetch(self, url, headers=None):
        if url.startswith("data:"):
            return super().fetch(url, headers)
        if url.startswith("file://"):
            path = Path(url2pathname(urlsplit(url).path)).resolve()  # also file:///C:/... on Windows
            if path.is_file() and FONT_DIR in path.parents:
                return super().fetch(url, headers)
        raise URLFetchingError(f"Blocked resource: {url[:60]}")


def font_faces():
    faces = [
        ("Inter", "Inter-Regular.ttf", 400), ("Inter", "Inter-SemiBold.ttf", 600), ("Inter", "Inter-Bold.ttf", 700),
        ("JetBrains Mono", "JetBrainsMono-Regular.ttf", 400), ("JetBrains Mono", "JetBrainsMono-Bold.ttf", 700),
        ("Noto Sans SC", "NotoSansSC-Regular.ttf", 400), ("Noto Sans SC", "NotoSansSC-Bold.ttf", 700),
    ]
    return "".join(
        f"@font-face{{font-family:'{family}';font-weight:{weight};src:url('{(FONT_DIR / file).as_uri()}') format('truetype')}}"
        for family, file, weight in faces
    )


def data_uri(field_file):
    try:
        with field_file.open("rb") as handle:
            raw = handle.read()
    except (OSError, ValueError):
        return ""
    mime = mimetypes.guess_type(field_file.name)[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def qr_uri(text):
    buffer = io.BytesIO()
    segno.make(text or " ", error="m").save(buffer, kind="svg", scale=4, border=0)
    return "data:image/svg+xml;base64," + base64.b64encode(buffer.getvalue()).decode()


def fill(text, record):
    """Replace {{key}} with the record's value."""
    def replace(match):
        return str(record["fields"].get(match.group(1).strip(), ""))

    return re.sub(r"\{\{\s*([\w.-]+)\s*\}\}", replace, text or "")


def esc(value):
    return html.escape(str(value if value is not None else ""))


def _style(el, defaults=None):
    parts = dict(defaults or {})
    if el.get("size"):
        parts["font-size"] = f"{float(el['size'])}pt"
    if el.get("bold"):
        parts["font-weight"] = "700"
    if el.get("align"):
        parts["text-align"] = el["align"]
    if el.get("color") and re.fullmatch(r"#[0-9a-fA-F]{3,8}", el["color"]):
        parts["color"] = el["color"]
    return ";".join(f"{k}:{v}" for k, v in parts.items())


def _fit_size(text, base, chars):
    """Long names shrink instead of breaking the layout."""
    length = len(text)
    return base if length <= chars else max(base * chars / length, base * 0.55)


def render_element(el, record, school):
    kind = el.get("type")
    if kind == "heading":
        text = esc(fill(el.get("text", ""), record))
        if el.get("style") == "plain":
            return f'<h2 class="plain" style="{_style(el)}">{text}</h2>'
        return f'<div class="bar" style="{_style(el)}">{text}</div>'
    if kind == "text":
        return f'<p style="{_style(el)}">{esc(fill(el.get("text", ""), record)).replace(chr(10), "<br>")}</p>'
    if kind == "name":
        name = record["title"]
        size = _fit_size(name, float(el.get("size") or 17), 26)
        return f'<h1 class="name bm" style="font-size:{size:.1f}pt">{esc(name)}</h1>'
    if kind == "field":
        value = record["fields"].get(el.get("key"), "")
        if el.get("hide_empty") and value in ("", None):
            return ""
        label = esc(el.get("label") or record["labels"].get(el.get("key"), el.get("key", "")))
        value_class = "value mono" if el.get("mono") else "value"
        if el.get("show_label") is False:
            return f'<div class="{value_class}" style="{_style(el)}">{esc(value)}</div>'
        return f'<div class="kv" style="{_style(el)}"><span class="key">{label}</span><span class="{value_class}">{esc(value)}</span></div>'
    if kind == "photo":
        width, height = float(el.get("w_mm") or 24), float(el.get("h_mm") or 33.6)
        if record.get("photo"):
            return f'<img class="photo" src="{record["photo"]}" alt="" style="width:{width}mm;height:{height}mm">'
        return f'<div class="photo empty" style="width:{width}mm;height:{height}mm"></div>'
    if kind == "logo":
        width = float(el.get("w_mm") or 20)
        if school["logo"]:
            return f'<div style="text-align:{el.get("align", "right")}"><img src="{school["logo"]}" alt="" style="width:{width}mm"></div>'
        return f'<div style="text-align:{el.get("align", "right")}"><div class="logo-box" style="width:{width}mm;height:{width}mm">{esc(school["short"])}</div></div>'
    if kind == "qr":
        size = float(el.get("size_mm") or 18)
        return f'<img src="{qr_uri(fill(el.get("value", ""), record))}" alt="" style="width:{size}mm;height:{size}mm">'
    if kind == "logins":
        return render_logins(el, record)
    if kind == "devices":
        return render_table(["Gerät", "Seriennummer", "Ausgabe", "Rückgabe"], record.get("devices", []))
    if kind == "cases":
        return render_table(["Datum", "Beschreibung", "Status", "Austauschgerät"], record.get("cases", []))
    if kind == "list":
        columns = el.get("columns") or []
        rows = [[member.get(c["key"], "") for c in columns] for member in record.get("members", [])]
        return render_table([c.get("title", c["key"]) for c in columns], rows, extra_class="list")
    if kind == "signature":
        lines = [line for line in (el.get("text") or "Unterschrift").split("\n") if line.strip()]
        cells = "".join(f'<div class="sign"><span>{esc(line)}</span></div>' for line in lines)
        return f'<div class="signs">{cells}</div>'
    if kind == "spacer":
        return f'<div style="height:{float(el.get("h_mm") or 5)}mm"></div>'
    if kind == "divider":
        return '<hr>'
    return ""


def render_logins(el, record):
    blocks = []
    for login in record.get("logins", []):
        if el.get("platforms") and login["key"] not in el["platforms"]:
            continue
        rows = login["rows"]
        if not any(r["value"] for r in rows) and el.get("hide_empty", True):
            continue
        body = "".join(
            f'<div class="r"><b>{esc(r["label"])}</b><code>{esc(r["value"])}</code></div>' for r in rows if r["value"]
        )
        blocks.append(f'<section><div class="bar">{esc(login["name"])}</div>{body}</section>')
    return "".join(blocks)


def render_table(headers, rows, extra_class=""):
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in row) + "</tr>" for row in rows)
    if not rows:
        body = f'<tr><td colspan="{len(headers)}" class="none">Keine Einträge</td></tr>'
    return f'<table class="grid {extra_class}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def render_rows(tree, record, school):
    out = []
    for row in tree.get("rows", []):
        cols = row.get("cols", [])
        total = sum(c.get("w", 12) for c in cols) or 12
        cells = []
        for col in cols:
            inner = "".join(render_element(el, record, school) for el in col.get("items", []))
            percent = 100 * col.get("w", 12) / total
            cells.append(f'<div class="col" style="flex:0 0 {percent:.3f}%;width:{percent:.3f}%;align-self:{col.get("valign", "flex-start")}">{inner}</div>')
        out.append(f'<div class="row">{"".join(cells)}</div>')
    return "".join(out)


BASE_CSS = """
*{box-sizing:border-box}
html{font-family:%(font)s;font-size:9.5pt;line-height:1.32;color:#17202a}
body{margin:0}
p{margin:0 0 1.5mm}
h1,h2,h3{margin:0;line-height:1.15}
.sheet{break-after:page}
.sheet:last-child{break-after:auto}
.bm{bookmark-level:1;bookmark-label:content(text)}
.name{font-weight:700}
h2.plain{font-size:11pt;font-weight:600;margin:0 0 1.5mm}
.row{display:flex;gap:0;margin:0 0 1.6mm}
.col{padding:0 2mm;min-width:0}
.col:first-child{padding-left:0}
.col:last-child{padding-right:0}
.bar{background:#17202a;color:#fff;font-weight:600;font-size:9pt;text-align:center;padding:1mm 3mm;margin:0}
section{break-inside:avoid;margin:0 0 2.2mm}
.r{display:flex;padding:.8mm 3mm;border-bottom:.15mm solid #d5dbe1}
.r b{width:34mm;flex:none;font-weight:600}
code,.mono{font-family:%(mono)s;font-size:10pt;font-variant-ligatures:none}
.kv{display:flex;gap:2mm;padding:.5mm 0}
.key{font-weight:600;width:32mm;flex:none}
.photo{display:block;object-fit:cover;border-radius:1mm}
.photo.empty{background:#e7ecf2}
.logo-box{border:.3mm solid #17202a;font-size:6.5pt;text-align:center;display:inline-flex;align-items:center;justify-content:center}
table.grid{width:100%%;border-collapse:collapse;font-size:8.5pt}
table.grid th{text-align:left;font-weight:600;border-bottom:.3mm solid #17202a;padding:.8mm 1.5mm}
table.grid td{border-bottom:.15mm solid #d5dbe1;padding:.8mm 1.5mm;vertical-align:top}
table.grid td.none{color:#667}
table.grid.list tr{break-inside:avoid}
hr{border:0;border-top:.2mm solid #b8c1cc;margin:2mm 0}
.signs{display:flex;gap:8mm;margin-top:14mm}
.sign{flex:1;border-top:.25mm solid #17202a;padding-top:1mm;font-size:8pt;color:#445}
img{max-width:100%%}
.nup{width:100%%;border-collapse:collapse;table-layout:fixed;page-break-after:always}
.nup:last-child{page-break-after:auto}
.nup td{vertical-align:top;overflow:hidden;padding:0}
.nup .cell{overflow:hidden}
.nup.cut td{border:.15mm dashed #b8c1cc}
""" % {"font": BASE_FONT, "mono": MONO_FONT}


def page_css(page, school, confidential):
    preset = PRESETS.get(page.get("preset"), PRESETS["a4"])
    margin = page.get("margin") or DEFAULT_PAGE["margin"]
    is_grid = "cols" in preset
    top, right, bottom, left = (0, 0, 0, 0) if is_grid or page.get("preset") == "card" else margin
    footer = ""
    if page.get("footer", True) and not is_grid and page.get("preset") != "card":
        text = esc(page.get("footer_text") if page.get("footer_text") is not None else (school["footer"] if confidential else ""))
        footer = (
            f"@bottom-left{{content:'{text.replace(chr(39), '')}';font-family:{BASE_FONT};font-size:7.5pt;color:#667}}"
            f"@bottom-right{{content:'Seite ' counter(page) ' von ' counter(pages);font-family:{BASE_FONT};font-size:7.5pt;color:#667}}"
        )
    return f"@page{{size:{preset['w']}mm {preset['h']}mm;margin:{top}mm {right}mm {bottom}mm {left}mm;{footer}}}"


def school_info():
    school = SchoolSettings.load()
    return {
        "name": school.name, "short": school.short_name, "footer": school.confidential_footer,
        "logo": data_uri(school.logo) if school.logo else "",
    }


def wrap_records(sheets, page):
    preset = PRESETS.get(page.get("preset"), PRESETS["a4"])
    if "cols" not in preset:
        return "".join(f'<div class="sheet" id="rec-{i}">{html_}</div>' for i, html_ in enumerate(sheets))
    cols, rows = preset["cols"], preset["rows"]
    pad_x, pad_y = preset.get("pad_x", 0), preset.get("pad_y", 0)
    cell_w = (preset["w"] - 2 * pad_x) / cols
    cell_h = (preset["h"] - 2 * pad_y) / rows - 0.25
    inner = float(page.get("cell_padding", 6 if preset.get("cut") else 3))
    per_page = cols * rows
    tables = []
    for start in range(0, len(sheets), per_page):
        chunk = sheets[start:start + per_page]
        chunk += [""] * (per_page - len(chunk))
        trs = []
        for r in range(rows):
            tds = "".join(
                f'<td style="width:{cell_w}mm;height:{cell_h}mm"><div class="cell" style="height:{cell_h}mm;padding:{inner}mm">{chunk[r * cols + c]}</div></td>'
                for c in range(cols)
            )
            trs.append(f"<tr>{tds}</tr>")
        cut = " cut" if preset.get("cut") else ""
        tables.append(f'<div style="padding:{pad_y}mm {pad_x}mm 0"><table class="nup{cut}">{"".join(trs)}</table></div>')
    return "".join(tables)


def code_env():
    env = ImmutableSandboxedEnvironment(autoescape=select_autoescape(default=True, default_for_string=True))
    env.globals["qr"] = lambda text: Markup(f'<img src="{qr_uri(str(text))}" alt="">')
    return env


def build_html(template_doc, records):
    """template_doc: dict with tree, code_mode, code_html, code_css."""
    school = school_info()
    tree = template_doc.get("tree") or {}
    page = {**DEFAULT_PAGE, **(tree.get("page") or {})}
    confidential = template_doc.get("contains_secrets", False)
    extra_css = ""
    if template_doc.get("code_mode"):
        env = code_env()
        compiled = env.from_string(template_doc.get("code_html") or "")
        sheets = [compiled.render(r=record, school=school, today=timezone.localdate()) for record in records]
        extra_css = template_doc.get("code_css") or ""
    else:
        sheets = [render_rows(tree, record, school) for record in records]
    css = font_faces() + BASE_CSS + page_css(page, school, confidential) + extra_css
    title = esc(template_doc.get("name", "Bericht"))
    return f'<!doctype html><html lang="de"><head><meta charset="utf-8"><title>{title}</title><style>{css}</style></head><body>{wrap_records(sheets, page)}</body></html>'


def render_markup(markup):
    """Lays out a finished page with the bundled fonts and the same blocked resources."""
    return HTML(string=markup, url_fetcher=SafeFetcher()).render(font_config=FontConfiguration())


def render_pdf(template_doc, records):
    """Returns (pdf bytes, page count, warnings)."""
    document = render_markup(build_html(template_doc, records))
    warnings = []
    preset = PRESETS.get(((template_doc.get("tree") or {}).get("page") or {}).get("preset"), PRESETS["a4"])
    if "cols" not in preset and template_doc.get("source") != "class":
        starts = {}
        for index, page in enumerate(document.pages):
            for anchor in page.anchors:
                if anchor.startswith("rec-"):
                    starts.setdefault(int(anchor[4:]), index)
        order = sorted(starts.items())
        for position, (rec, start) in enumerate(order):
            end = order[position + 1][1] if position + 1 < len(order) else len(document.pages)
            if end - start > 1:
                warnings.append(f"{records[rec]['title']}: Das Blatt läuft auf {end - start} Seiten.")
    stream = io.BytesIO()
    document.write_pdf(stream)
    return stream.getvalue(), len(document.pages), warnings
