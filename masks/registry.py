"""Built-in fields and the blocks a mask can place. Admins add more at runtime."""

from django.utils.translation import gettext_lazy as _

GENDER = ["Weiblich", "Männlich", "Divers"]
SCHOOL_TYPES = [("GS", "#16a34a"), ("OS", "#0891b2"), ("RS", "#7c3aed"), ("GY", "#2553eb"), ("Kiga", "#db2777"), ("Gast", "#d97706")]
STAFF_CATEGORIES = ["Schulleitung", "Lehrpersonal", "Verwaltung", "Kindergarten", "Technik", "Praktikant", "Besucher"]


def _opts(values):
    return [{"value": v[0], "color": v[1]} if isinstance(v, tuple) else {"value": v, "color": ""} for v in values]


COMMON = [
    ("first_name", "Vorname", "text", {"required": True, "in_grid": True}),
    ("last_name", "Nachname", "text", {"required": True, "in_grid": True}),
    ("family_name_first", "Familienname zuerst", "bool", {"help_text": "Für Namen wie 王小明: Familienname wird zuerst gedruckt."}),
    ("gender", "Geschlecht", "choice", {"options": _opts(GENDER)}),
    ("birth_date", "Geburtsdatum", "date", {"in_grid": True}),
    ("email", "E-Mail", "email", {"in_grid": True}),
    ("entry_date", "Eintritt", "date", {"in_grid": True}),
    ("exit_date", "Austritt", "date", {}),
    ("notes", "Bemerkungen", "textarea", {}),
    ("archived", "Archiviert", "bool", {}),
]

PUPIL = [
    ("klasse", "Klasse", "class", {"in_grid": True}),
    ("school_type", "Schultyp", "choice", {"options": _opts(SCHOOL_TYPES), "in_grid": True}),
    ("grade_level", "Klassenstufe", "number", {}),
    ("birth_place", "Geburtsort", "text", {}),
    ("dyslexia", "Lese-/Rechtschreibschwäche", "bool", {}),
    ("bus", "Bus", "bool", {}),
    ("new_codes_needed", "Neue Codes/Accounts", "bool", {}),
]

STAFF = [
    ("category", "Kategorie", "choice", {"options": _opts(STAFF_CATEGORIES), "in_grid": True}),
    ("salutation", "Anrede", "choice", {"options": _opts(["Frau", "Herr"])}),
    ("abbreviation", "Kürzel", "text", {"in_grid": True}),
    ("mobile", "Mobiltelefon", "phone", {"in_grid": True}),
    ("phone_private", "Tel. privat", "phone", {}),
    ("phone_office", "Tel. Büro", "phone", {}),
    ("phone_emergency", "Tel. Notruf", "phone", {}),
    ("private_email", "E-Mail privat", "email", {}),
    ("compound", "Compound", "text", {}),
    ("street", "Straße / Nr.", "text", {}),
    ("district", "Bezirk", "text", {}),
    ("city", "Stadt / PLZ", "text", {}),
    ("street_office", "Straßenbüro", "text", {}),
    ("daz_staff", "DaZ-Fachkraft", "bool", {"help_text": "Steht dann im DaZ-Bereich als Lehrkraft zur Auswahl."}),
    ("kiga_staff", "Kindergarten-Fachkraft", "bool", {"help_text": "Steht dann im Kindergarten-Bereich als Fachkraft zur Auswahl."}),
]

BLOCKS = {
    "photo": (_("Foto"), "image"),
    "card": (_("Ausweiskarte"), "card"),
    "enrollments": (_("Klassenverlauf"), "history"),
    "guardians": (_("Eltern"), "heart"),
    "devices": (_("Geräte"), "laptop"),
    "cases": (_("Schäden und Support"), "wrench"),
    "logins": (_("Zugänge"), "key"),
    "attachments": (_("Dateien"), "paperclip"),
    "comments": (_("Kommentare"), "message"),
    "history": (_("Änderungsverlauf"), "history"),
}  # modules add theirs (core/modules.py add_block)


def f(key, w=6):
    return {"type": "field", "field": key, "w": w}


def b(key, w=12):
    return {"type": "block", "block": key, "w": w}


def default_tree(entity):
    if entity == "pupil":
        return {"tabs": [
            {"id": "t1", "title": "Stammdaten", "sections": [
                {"id": "s1", "title": "Allgemeine Daten", "items": [
                    f("first_name", 6), f("last_name", 6), f("klasse", 3), f("school_type", 3), f("grade_level", 3), f("gender", 3),
                    f("birth_date", 3), f("birth_place", 3), f("entry_date", 3), f("exit_date", 3),
                    f("dyslexia", 3), f("bus", 3), f("family_name_first", 3), f("archived", 3), f("notes", 12),
                ]},
                {"id": "s2", "title": "Foto und Karte", "items": [b("photo", 6), b("card", 6)]},
            ]},
            {"id": "t2", "title": "IT Informationen", "sections": [
                {"id": "s3", "title": "Konto", "items": [f("email", 6), f("new_codes_needed", 6)]},
                {"id": "s4", "title": "Geräte", "items": [b("devices")]},
                {"id": "s5", "title": "Bisherige Supportfälle", "items": [b("cases")]},
            ]},
            {"id": "t3", "title": "eBooks & Passwörter", "sections": [{"id": "s6", "title": "", "items": [b("logins")]}]},
            {"id": "t4", "title": "Eltern", "sections": [{"id": "s7", "title": "", "items": [b("guardians")]}]},
            {"id": "t5", "title": "Klasse & Verlauf", "sections": [{"id": "s8", "title": "", "items": [b("enrollments", 6), b("history", 6)]}]},
            {"id": "t6", "title": "Dateien & Notizen", "sections": [{"id": "s9", "title": "", "items": [b("attachments", 6), b("comments", 6)]}]},
        ]}
    return {"tabs": [
        {"id": "t1", "title": "Grunddaten", "sections": [
            {"id": "s1", "title": "Allgemeine Daten", "items": [
                f("category", 6), f("salutation", 3), f("gender", 3), f("first_name", 6), f("last_name", 6), f("abbreviation", 3),
                f("birth_date", 3), f("entry_date", 3), f("exit_date", 3), f("mobile", 3), f("phone_private", 3), f("phone_office", 3),
                f("phone_emergency", 3), f("private_email", 12), f("archived", 3), f("daz_staff", 3), f("kiga_staff", 3), f("notes", 12),
            ]},
            {"id": "s2", "title": "Foto und Karte", "items": [b("photo", 6), b("card", 6)]},
        ]},
        {"id": "t2", "title": "Anschrift in China", "sections": [{"id": "s3", "title": "", "items": [
            f("compound", 6), f("street", 6), f("district", 6), f("city", 6), f("street_office", 6),
        ]}]},
        {"id": "t3", "title": "IT Informationen", "sections": [
            {"id": "s4", "title": "Konto", "items": [f("email", 6)]},
            {"id": "s5", "title": "Surface oder iPad", "items": [b("devices")]},
            {"id": "s6", "title": "Bisherige Supportfälle", "items": [b("cases")]},
        ]},
        {"id": "t4", "title": "Zugänge", "sections": [{"id": "s7", "title": "", "items": [b("logins")]}]},
        {"id": "t5", "title": "Dateien & Notizen", "sections": [{"id": "s8", "title": "", "items": [b("attachments", 6), b("comments", 6)]}]},
    ]}
