"""Idempotent starting configuration, modelled on the current Ninox database."""
from core.models import SavedView, SchoolSettings
from devices.models import AssetCategory
from prints.models import PrintTemplate
from vault.models import Platform

from .builtin_names import fill_options, fill_tree, names_for
from .models import FieldDefinition, Mask, MaskVersion
from .registry import COMMON, PUPIL, STAFF, default_tree

UP = [{"key": "username", "label": "Benutzername"}, {"key": "password", "label": "Passwort", "secret": True}]
PLATFORMS = [
    ("m365", "Windows / Office 365 / Teams", [{"key": "username", "label": "Account-Name"}, {"key": "password", "label": "Passwort", "secret": True}], ["pupil", "staff"], "https://www.office.com"),
    ("bibox", "Westermann Bibox", UP, ["pupil"], "https://bibox2.westermann.de"),
    ("anton", "Anton", [{"key": "code", "label": "Zugangscode", "secret": True}], ["pupil"], "https://anton.app"),
    ("cornelsen", "Cornelsen", UP, ["pupil"], "https://www.cornelsen.de"),
    ("klett", "Klett", UP, ["pupil"], "https://www.klett.de"),
    ("buchner", "Buchner Click & Study", UP, ["pupil"], "https://www.click-and-study.de"),
    ("untis", "Untis", UP, ["pupil"], "https://webuntis.com"),
    ("fobizz", "Fobizz", [{"key": "code", "label": "Code", "secret": True}, {"key": "kuerzel", "label": "Kürzel"}], ["pupil"], "https://fobizz.com"),
    ("untis-eltern", "Untis für Eltern", UP, ["guardian"], "https://webuntis.com"),
    ("calendly", "Calendly", [{"key": "username", "label": "E-Mail"}, {"key": "password", "label": "Passwort", "secret": True}], ["staff"], "https://calendly.com"),
]
CATEGORIES = [
    ("Surface", "SuS", "", 4, 1300), ("Lehrergerät", "Lehrer", "", 4, 1200), ("iPad (MS)", "MS", "-", 3, 100),
    ("Gastgerät (GS)", "GS", "-", 3, 400), ("Kindergarten (KG)", "KG", "-", 3, 20),
]


def el(type_, **kw):
    return {"type": type_, **kw}


def row(*cols, valign=None):
    built = [{"w": w, "items": items} for w, items in cols]
    if valign:
        for col in built:
            col["valign"] = valign
    return {"cols": built}


def _header():
    return row(
        (2, [el("photo", w_mm=24, h_mm=33.6)]),
        (8, [el("name", size=17), el("text", text="Klasse {{klasse}}", color="#556677")]),
        (2, [el("logo", w_mm=22)]),
        valign="center",
    )


def _kv(*keys):
    return [el("field", key=k) for k in keys]


TEMPLATES = [
    ("Zugangsdaten Schüler", "Ersetzt das Ninox-Layout Passwörter", "pupil", {"page": {"preset": "a4"}, "rows": [
        _header(), row((12, [el("logins", hide_empty=True)])),
        row((2, [el("qr", value="https://www.office.com", size_mm=16)]), (10, [el("text", text="Anmeldung: QR-Code scannen. Bitte das Passwort beim ersten Login ändern.", size=8)]), valign="center"),
    ]}),
    ("Zugangskärtchen (4 pro Blatt)", "Microsoft-Zugang zum Zuschneiden", "pupil", {"page": {"preset": "a4-4up"}, "rows": [
        row((12, [el("name", size=13), el("text", text="Klasse {{klasse}}", size=8)])),
        row((12, [el("logins", platforms=["m365"], hide_empty=False)])),
        row((12, [el("text", text="{{schule}}", size=7, color="#667788")])),
    ]}),
    ("Stammblatt Schüler", "Ersetzt das Ninox-Layout Schüler", "pupil", {"page": {"preset": "a4", "footer_text": ""}, "rows": [
        _header(), row((12, [el("heading", text="Allgemeine Daten")])),
        row((6, _kv("first_name", "last_name", "klasse", "birth_date", "birth_place", "gender")), (6, _kv("entry_date", "exit_date", "grade_level", "school_type", "bus", "dyslexia"))),
        row((12, [el("heading", text="IT Informationen")])), row((12, _kv("email", "geraet", "seriennummer"))),
        row((12, [el("devices")])), row((12, [el("heading", text="Schäden und Support")])), row((12, [el("cases")])),
    ]}),
    ("SuS", "Ninox-Layout SuS: Kopfbereich", "pupil", {"page": {"preset": "a4", "footer_text": ""}, "rows": [_header()]}),
    ("Stammblatt Personal", "Ersetzt das Ninox-Layout Personal", "staff", {"page": {"preset": "a4", "footer_text": ""}, "rows": [
        row((2, [el("photo", w_mm=24, h_mm=33.6)]), (8, [el("name", size=17), el("text", text="{{category}}", color="#556677")]), (2, [el("logo", w_mm=22)]), valign="center"),
        row((12, [el("heading", text="Grunddaten")])),
        row((6, _kv("salutation", "abbreviation", "birth_date", "entry_date", "exit_date")), (6, _kv("mobile", "phone_private", "phone_office", "phone_emergency", "private_email"))),
        row((12, [el("heading", text="Anschrift in China")])), row((6, _kv("compound", "street", "street_office")), (6, _kv("district", "city"))),
        row((12, [el("heading", text="IT Informationen")])), row((12, _kv("email"))), row((12, [el("devices")])),
    ]}),
    ("Zugangsdaten Personal", "Alle Zugänge einer Lehrkraft", "staff", {"page": {"preset": "a4"}, "rows": [
        row((2, [el("photo", w_mm=24, h_mm=33.6)]), (8, [el("name", size=17), el("text", text="{{category}}", color="#556677")]), (2, [el("logo", w_mm=22)]), valign="center"),
        row((12, [el("logins", hide_empty=True)])),
    ]}),
    ("Untis-Zugang Eltern", "Ersetzt Untis Passwort Eltern, jetzt je Elternteil", "guardian", {"page": {"preset": "a4"}, "rows": [
        row((10, [el("name", size=17), el("text", text="Kinder: {{children}}", color="#556677")]), (2, [el("logo", w_mm=22)])),
        row((12, [el("spacer", h_mm=4), el("logins", hide_empty=False)])),
        row((12, [el("text", text="Die Anmeldung ist in der App Untis Mobile oder im Browser möglich. Bitte das Passwort beim ersten Login ändern.")])),
    ]}),
    ("Klassenliste", "Liste aller Schüler einer Klasse", "class", {"page": {"preset": "a4l", "footer_text": ""}, "rows": [
        row((10, [el("name", size=16), el("text", text="Schuljahr {{schuljahr}} · {{anzahl}} Schüler · Stand {{datum}}", color="#556677")]), (2, [el("logo", w_mm=18)]), valign="center"),
        row((12, [el("list", columns=[{"key": "sort_name", "title": "Name"}, {"key": "email", "title": "E-Mail"}, {"key": "birth_date", "title": "Geburtsdatum"}, {"key": "device", "title": "Gerät"}, {"key": "empty", "title": "Notiz"}])])),
    ]}),
    ("Geräte-Übergabeprotokoll", "Mit Unterschriften von Schüler und Eltern", "asset", {"page": {"preset": "a4", "footer_text": ""}, "rows": [
        row((10, [el("heading", text="Geräte-Übergabeprotokoll", style="plain", size=16), el("text", text="{{schule}}", color="#556677")]), (2, [el("logo", w_mm=20)])),
        row((12, [el("heading", text="Gerät")])), row((6, _kv("number", "category", "serial")), (6, _kv("pen", "keyboard", "issued"))),
        row((12, [el("heading", text="Empfänger")])), row((12, _kv("holder", "holder_class"))),
        row((12, [el("text", text="Das Gerät ist Eigentum der Schule und wird in einwandfreiem Zustand übergeben. Schäden sind unverzüglich der IT zu melden.")])),
        row((12, [el("signature", text="Datum, Unterschrift Schüler/in\nDatum, Unterschrift Erziehungsberechtigte\nDatum, Unterschrift IT")])),
    ]}),
    ("Geräte-Etiketten", "Nummer und QR-Code auf Etikettenbogen", "asset", {"page": {"preset": "label-3474"}, "rows": [
        row((5, [el("qr", value="{{number}}", size_mm=22)]), (7, [el("field", key="number", show_label=False, bold=True, mono=True, size=11), el("field", key="category", show_label=False, size=7.5), el("field", key="serial", show_label=False, size=6.5, mono=True)])),
    ]}),
    ("Schadensbericht", "Für Versicherung oder Eltern", "case", {"page": {"preset": "a4", "footer_text": ""}, "rows": [
        row((10, [el("heading", text="Schadensbericht", style="plain", size=16), el("text", text="{{schule}}", color="#556677")]), (2, [el("logo", w_mm=20)])),
        row((6, _kv("person", "klasse", "date", "status")), (6, _kv("number", "serial", "replacement_number", "replacement_serial"))),
        row((12, [el("heading", text="Beschreibung")])), row((12, [el("text", text="{{description}}")])), row((12, _kv("parts"))),
        row((12, [el("signature", text="Datum, Unterschrift IT\nDatum, Unterschrift Erziehungsberechtigte")])),
    ]}),
]

VIEWS = [
    ("pupil", "Ohne Gerät", {"scope": "active", "preset": "ohne-geraet"}),
    ("pupil", "Fehlende E-Mail", {"scope": "active", "preset": "ohne-email"}),
    ("pupil", "Fehlendes Foto", {"scope": "active", "preset": "ohne-foto"}),
    ("pupil", "Neue Codes nötig", {"scope": "active", "preset": "neue-codes"}),
    ("pupil", "Ehemalige", {"scope": "archived"}),
    ("staff", "Ehemalige Mitarbeiter", {"scope": "archived"}),
]


def contains_logins(tree):
    return any(item.get("type") == "logins" for row_ in tree.get("rows", []) for col in row_.get("cols", []) for item in col.get("items", []))


def setup():
    SchoolSettings.load()
    for entity, extra in (("pupil", PUPIL), ("staff", STAFF)):
        for order, (key, label, type_, opts) in enumerate(COMMON + extra):
            opts = {**opts, "options": fill_options(opts.get("options"))} if opts.get("options") else opts
            FieldDefinition.objects.get_or_create(entity=entity, key=key, defaults={"label": label, "labels": names_for(label), "type": type_, "is_core": True, "order": order * 10, **opts})
        name = "Schüler" if entity == "pupil" else "Personal"
        mask, created = Mask.objects.get_or_create(entity=entity, is_default=True, defaults={"name": name, "names": names_for(name)})
        if created:
            tree = default_tree(entity)
            fill_tree(tree)
            MaskVersion.objects.create(mask=mask, number=1, status=MaskVersion.PUBLISHED, tree=tree)
    for order, (key, name, fields, applies, url) in enumerate(PLATFORMS):
        Platform.objects.get_or_create(key=key, defaults={"name": name, "fields": fields, "applies_to": applies, "login_url": url, "order": (order + 1) * 10, "on_sheet": key != "calendly"})
    for name, prefix, sep, digits, start in CATEGORIES:
        AssetCategory.objects.get_or_create(name=name, defaults={"prefix": prefix, "separator": sep, "digits": digits, "next_number": start})
    for name, description, source, tree in TEMPLATES:
        PrintTemplate.objects.get_or_create(name=name, defaults={"description": description, "source": source, "tree": tree, "contains_secrets": contains_logins(tree)})
    for entity, name, config in VIEWS:
        SavedView.objects.get_or_create(entity=entity, name=name, owner=None, defaults={"config": config})
