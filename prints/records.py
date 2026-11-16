"""Builds the data each template sees. Secrets are only decrypted when requested."""
from django.db.models import Prefetch
from django.utils import timezone
from django.utils.translation import gettext as _

from core.models import SchoolSettings
from devices.models import Asset, Assignment, SupportCase
from masks import services as ms
from people.models import ClassGroup, Guardian, Person
from vault.models import Credential, Platform

from .engine import data_uri

MASK = "••••••••"
GUARDIAN_LABELS = {"first_name": "Vorname", "last_name": "Nachname", "email": "E-Mail", "phone": "Telefon", "children": "Kinder"}
ASSET_LABELS = {
    "number": "Gerätenummer", "serial": "Seriennummer", "category": "Gerätetyp", "uuid": "UUID", "holder": "Ausgegeben an",
    "holder_class": "Klasse", "issued": "Ausgabe", "pen": "Stift", "keyboard": "Tastatur", "notes": "Bemerkungen", "status": "Status",
}
CASE_LABELS = {
    "description": "Beschreibung", "date": "Datum", "status": "Status", "parts": "Schaden an", "person": "Name", "klasse": "Klasse",
    "number": "Gerätenummer", "serial": "Seriennummer", "replacement_number": "Austauschgerät", "replacement_serial": "Seriennummer Austausch",
}
LIST_COLUMNS = [
    ("sort_name", "Name"), ("email", "E-Mail"), ("birth_date", "Geburtsdatum"), ("school_type", "Schultyp"), ("device", "Gerät"),
    ("serial", "Seriennummer"), ("empty", "Notiz"),
]


def _d(value):
    return value.strftime("%d.%m.%Y") if value else ""


def common_fields():
    school = SchoolSettings.load()
    return {"schule": school.name, "datum": _d(timezone.localdate())}


def _logins(owner_filter, kinds, include_secrets):
    platforms = Platform.objects.filter(active=True, on_sheet=True)
    existing = {c.platform_id: c for c in Credential.objects.filter(**owner_filter).select_related("platform")}
    out = []
    for platform in platforms:
        if not set(platform.applies_to) & kinds:
            continue
        credential = existing.get(platform.pk)
        rows = credential.read_all(include_secrets) if credential else [
            {"key": f["key"], "label": f["label"], "secret": bool(f.get("secret")), "value": ""} for f in platform.fields
        ]
        out.append({"key": platform.key, "name": platform.name, "rows": rows, "by_key": {r["key"]: r["value"] for r in rows}, "url": platform.login_url})
    return out


def person_record(person, include_secrets=False):
    from core.context_user import get_current_user

    fields = {**common_fields()}
    labels = {}
    for fd in ms.fields_for(person.kind, user=get_current_user()):  # without what the printing role does not see
        if fd.type == "secret":
            continue
        labels[fd.key] = fd.label
        fields[fd.key] = ms.display_value(person, fd)
    fields["name"] = person.display_name
    labels["name"] = "Name"
    active_card = person.cards.filter(active=True).first()
    fields["kartennummer"] = active_card.code if active_card else ""
    labels["kartennummer"] = "Kartennummer"
    assignments = list(person.assignments.select_related("asset"))
    current = [a for a in assignments if a.returned_at is None]
    fields["geraet"] = ", ".join(a.asset.number for a in current)
    fields["seriennummer"] = ", ".join(a.asset.serial for a in current if a.asset.serial)
    labels.update(geraet="Gerät", seriennummer="Seriennummer")
    return {
        "id": str(person.pk), "title": person.display_name, "fields": fields, "labels": labels,
        "photo": data_uri(person.photo) if person.photo else "",
        "logins": _logins({"person": person}, {person.kind}, include_secrets),
        "devices": [[a.asset.number, a.asset.serial, _d(a.issued_at), _d(a.returned_at)] for a in assignments],
        "cases": [[_d(c.date), c.description, c.status, c.replacement_number] for c in person.support_cases.all()],
    }


def guardian_record(guardian, include_secrets=False):
    children = [link.person for link in guardian.guardianships.select_related("person", "person__current_class")]
    names = ", ".join(f"{c.display_name}{f' ({c.current_class.name})' if c.current_class else ''}" for c in children)
    fields = {**common_fields(), "first_name": guardian.first_name, "last_name": guardian.last_name, "email": guardian.email, "phone": guardian.phone, "children": names, "name": guardian.display_name}
    return {
        "id": str(guardian.pk), "title": guardian.display_name, "fields": fields, "labels": {**GUARDIAN_LABELS, "name": "Name"},
        "photo": "", "logins": _logins({"guardian": guardian}, {"guardian"}, include_secrets), "devices": [], "cases": [],
    }


def asset_record(asset):
    current = asset.assignments.select_related("person", "person__current_class", "place").filter(returned_at__isnull=True).first()
    person = current.person if current else None
    fields = {
        **common_fields(), "number": asset.number, "serial": asset.serial, "category": asset.category.name if asset.category else "",
        "uuid": asset.hardware_uuid, "status": asset.status, "notes": asset.notes,
        "holder": str(current.holder) if current else "", "holder_class": person.current_class.name if person and person.current_class else "",
        "issued": _d(current.issued_at) if current else "", "pen": current.pen if current else "", "keyboard": current.keyboard if current else "",
    }
    return {"id": str(asset.pk), "title": asset.number, "fields": fields, "labels": ASSET_LABELS, "photo": "", "logins": [], "devices": [], "cases": []}


def case_record(case):
    fields = {
        **common_fields(), "description": case.description, "date": _d(case.date), "status": case.status, "parts": ", ".join(case.parts),
        "person": case.person.display_name if case.person else "", "klasse": case.person.current_class.name if case.person and case.person.current_class else "",
        "number": case.asset.number if case.asset else "", "serial": case.asset.serial if case.asset else "",
        "replacement_number": case.replacement_number, "replacement_serial": case.replacement_serial,
    }
    return {"id": str(case.pk), "title": f"Schaden {_d(case.date)}", "fields": fields, "labels": CASE_LABELS, "photo": "", "logins": [], "devices": [], "cases": []}


def class_record(group):
    members = []
    query = group.members.filter(archived=False).prefetch_related(
        Prefetch("assignments", Assignment.objects.filter(returned_at__isnull=True).select_related("asset"), to_attr="open_assignments")
    )
    from core import fieldrules
    from core.context_user import get_current_user

    unseen = fieldrules.hidden(get_current_user(), "pupil")
    for person in query:
        assets = [a.asset for a in person.open_assignments]
        member = {
            "sort_name": person.sort_name, "email": person.email, "birth_date": _d(person.birth_date), "school_type": person.school_type,
            "device": ", ".join(a.number for a in assets), "serial": ", ".join(a.serial for a in assets), "empty": "",
        }
        members.append({key: "" if key in unseen else value for key, value in member.items()})
    fields = {**common_fields(), "name": group.name, "klasse": group.name, "anzahl": str(len(members)), "schuljahr": str(group.school_year or "")}
    return {"id": str(group.pk), "title": f"Klasse {group.name}", "fields": fields, "labels": {"klasse": "Klasse", "anzahl": "Schüler", "schuljahr": "Schuljahr", "name": "Name"}, "photo": "", "logins": [], "devices": [], "cases": [], "members": members}


PLATFORM_SOURCES = ("pupil", "staff", "guardian", "asset", "case", "class")
# What a role needs to print from a source of the platform (roles grant pupils, staff and parents apart).
SOURCE_RIGHTS = {"pupil": "pupil.view", "staff": "staff.view", "guardian": "guardian.view", "class": "pupil.view",
                 "asset": "device.view", "case": "device.view"}


def available_sources(user=None):
    """[(source, label)] of the platform, the loaded modules and the own tables (those `user` may read)."""
    from tables import services as table_services
    from tables.models import Table

    from .models import PrintTemplate

    from core import modules

    basic = "schulverwaltung" in modules.active()  # people, devices and classes
    sources = [(key, label) for key, label in PrintTemplate.SOURCES if (key in PLATFORM_SOURCES and basic) or (key not in PLATFORM_SOURCES and module_source(key))]
    if user is not None:
        sources = [(key, label) for key, label in sources if key not in SOURCE_RIGHTS or user.can(SOURCE_RIGHTS[key])]
    tables = table_services.readable(user) if user is not None else Table.objects.filter(archived=False)
    return sources + [(f"table:{t.key}", _("Tabelle: %(name)s") % {"name": t.display_name}) for t in tables]


def module_source(source):
    """A data source a module brings, such as report cards from Unterricht und Noten (core/modules.py print_sources),
    or an own table ("table:<key>", tables/prints.py)."""
    if (source or "").startswith("table:"):
        from tables.prints import source_for

        return source_for(source)
    from core import modules

    return modules.merged("print_sources").get(source)


def load_records(source, ids, include_secrets=False, class_id=None):
    if extra := module_source(source):
        return extra.load_records(ids, class_id)
    if source in ("pupil", "staff"):
        people = Person.objects.filter(kind=source, pk__in=ids).select_related("current_class").order_by("current_class__level", "current_class__name", "last_name", "first_name")
        return [person_record(p, include_secrets) for p in people]
    if source == "guardian":
        return [guardian_record(g, include_secrets) for g in Guardian.objects.filter(pk__in=ids)]
    if source == "asset":
        return [asset_record(a) for a in Asset.objects.filter(pk__in=ids).select_related("category")]
    if source == "case":
        return [case_record(c) for c in SupportCase.objects.filter(pk__in=ids).select_related("person", "person__current_class", "asset")]
    if source == "class":
        groups = ClassGroup.objects.filter(pk=class_id) if class_id else ClassGroup.objects.filter(pk__in=ids)
        return [class_record(g) for g in groups]
    return []


def field_catalog(source):
    """Fields the designer offers for a data source."""
    if extra := module_source(source):
        return extra.field_catalog() + [{"key": "schule", "label": "Name der Schule"}, {"key": "datum", "label": "Heutiges Datum"}]
    if source in ("pupil", "staff"):
        items = [{"key": "name", "label": "Name (komplett)"}]
        items += [{"key": f.key, "label": f.label} for f in ms.fields_for(source) if f.type != "secret"]
        items += [{"key": "geraet", "label": "Gerät"}, {"key": "seriennummer", "label": "Seriennummer"}, {"key": "kartennummer", "label": "Kartennummer"}]
    elif source == "guardian":
        items = [{"key": k, "label": v} for k, v in GUARDIAN_LABELS.items()]
    elif source == "asset":
        items = [{"key": k, "label": v} for k, v in ASSET_LABELS.items()]
    elif source == "case":
        items = [{"key": k, "label": v} for k, v in CASE_LABELS.items()]
    else:
        items = [{"key": "klasse", "label": "Klasse"}, {"key": "anzahl", "label": "Schüler"}, {"key": "schuljahr", "label": "Schuljahr"}]
    return items + [{"key": "schule", "label": "Name der Schule"}, {"key": "datum", "label": "Heutiges Datum"}]


SAMPLE_LOGINS = [
    ("m365", "Windows / Office 365 / Teams", [("Account-Name", "lena.mueller", False), ("Passwort", "Tomika-47", True)]),
    ("bibox", "Westermann Bibox", [("Benutzername", "lena.mueller", False), ("Passwort", "Rasuko-82", True)]),
    ("anton", "Anton", [("Zugangscode", "Beku-Mino-14", True)]),
    ("cornelsen", "Cornelsen", [("Benutzername", "lena.mueller", False), ("Passwort", "Hagipo-35", True)]),
]


def list_columns(source):
    """The columns a list element offers: the class list's, or those of a module's source (a report card's grade table)."""
    if extra := module_source(source):
        return extra.list_columns
    return LIST_COLUMNS


def sample_record(source):
    """Invented data for the designer preview, so nobody's password appears there."""
    if extra := module_source(source):
        return extra.sample_record()
    base = common_fields()
    if source in ("pupil", "staff", "guardian"):
        fields = {
            **base, "name": "Lena Müller-Šimić", "first_name": "Lena", "last_name": "Müller-Šimić", "klasse": "7b", "school_type": "GY", "birth_date": "14.03.2013",
            "email": "lena.mueller@schule.example", "category": "Lehrpersonal", "geraet": "SuS1042", "seriennummer": "005190920853", "kartennummer": "3735928559",
            "children": "Lena Müller-Šimić (7b)", "phone": "+49 151 0000 0000", "entry_date": "01.08.2022", "birth_place": "Berlin", "gender": "Weiblich",
        }
        labels = {f["key"]: f["label"] for f in field_catalog(source)}
        logins = [
            {"key": key, "name": name, "rows": [{"key": label, "label": label, "secret": secret, "value": value} for label, value, secret in rows]}
            for key, name, rows in SAMPLE_LOGINS
        ]
        logins = [l for l in logins] if source != "guardian" else [
            {"key": "untis-eltern", "name": "Untis für Eltern", "rows": [{"key": "u", "label": "Benutzername", "secret": False, "value": "mueller.eltern"}, {"key": "p", "label": "Passwort", "secret": True, "value": "Rasuko-82"}]}
        ]
        return {"id": "sample", "title": fields["name"], "fields": fields, "labels": labels, "photo": "", "logins": logins,
                "devices": [["SuS1042", "005190920853", "01.08.2024", ""]], "cases": [["03.03.2026", "Display gesprungen", "Erledigt", "SuS1188"]]}
    if source == "asset":
        fields = {**base, "number": "SuS1042", "serial": "005190920853", "category": "Surface", "uuid": "4C4C4544-0044", "status": "Ausgegeben", "holder": "Lena Müller-Šimić", "holder_class": "7b", "issued": "01.08.2024", "pen": "Ja", "keyboard": "Ja", "notes": ""}
        return {"id": "sample", "title": fields["number"], "fields": fields, "labels": ASSET_LABELS, "photo": "", "logins": [], "devices": [], "cases": []}
    if source == "case":
        fields = {**base, "description": "Display nach Sturz gesprungen, Touch reagiert unten rechts nicht mehr.", "date": "03.03.2026", "status": "In Reparatur", "parts": "Display", "person": "Lena Müller-Šimić", "klasse": "7b", "number": "SuS1042", "serial": "005190920853", "replacement_number": "SuS1188", "replacement_serial": "005525920853"}
        return {"id": "sample", "title": "Schaden 03.03.2026", "fields": fields, "labels": CASE_LABELS, "photo": "", "logins": [], "devices": [], "cases": []}
    members = [{"sort_name": n, "email": f"{i}@schule.example", "birth_date": "14.03.2013", "school_type": "GY", "device": f"SuS10{i:02d}", "serial": "005190920853", "empty": ""} for i, n in enumerate(["Dvořák, Tomáš", "Müller-Šimić, Lena", "Nguyễn, Anh Thư", "王, 小明", "Yılmaz, Çağla"], 1)]
    fields = {**base, "name": "7b", "klasse": "7b", "anzahl": "5", "schuljahr": "2026/27"}
    return {"id": "sample", "title": "Klasse 7b", "fields": fields, "labels": {"klasse": "Klasse", "anzahl": "Schüler", "schuljahr": "Schuljahr", "name": "Name"}, "photo": "", "logins": [], "devices": [], "cases": [], "members": members}
