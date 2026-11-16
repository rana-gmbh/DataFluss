"""Einstellungen → Benutzer und Rollen → Rollen: create roles and set their rights (core/rights.py).

A role is edited as areas (no access / read / edit) with switches, the own tables it may read or edit, and
(later) the fields it may only read or not see. IT is fixed. Someone who manages roles without being IT
may only grant what they may do themselves, so the page can never be used to gain rights.
"""
import json

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from masks import i18n

from . import audit, rights
from .context import NAV
from .models import Role, User, UserRole
from .permissions import require, role_capabilities

UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"})

# Starting points for a new role, besides empty and a copy of an existing role.
PRESETS = {
    "read": (_("Alles lesen"), lambda: ({a.key: rights.READ for a in rights.areas() if a.has_levels}, [])),
    "janitor": (_("Hausmeister"), lambda: ({"home": rights.READ, "pupils": rights.READ, "staff": rights.READ, "devices": rights.EDIT,
                                            "print": rights.READ}, [])),
    "office": (_("Büro"), lambda: ({"home": rights.READ, "pupils": rights.EDIT, "staff": rights.EDIT, "guardians": rights.EDIT,
                                    "devices": rights.READ, "print": rights.READ, "data": rights.EDIT},
                                   ["pupils.archive", "pupils.classes", "staff.archive"])),
}


def _tables():
    from tables.models import Table

    return list(Table.objects.filter(archived=False))


def _groups(entity, fields):
    """Fields in the order and sections of the entity's default mask (tabs and sections), then the rest."""
    from masks.models import Mask
    from tables.models import Table

    by_key = {f.key: f for f in fields}
    if entity.startswith("table:"):
        mask = Mask.objects.filter(table__key=entity.removeprefix("table:"), is_default=True).first() \
            or Mask.objects.filter(table__key=entity.removeprefix("table:")).first()
    else:
        mask = Mask.objects.filter(entity=entity, table=None, is_default=True).first()
    version = mask.published if mask else None
    groups, placed = [], set()
    for tab in (version.tree if version else {}).get("tabs", []):
        for section in tab.get("sections", []):
            keys = [i.get("field") for i in section.get("items", []) if i.get("type") == "field" and i.get("field") in by_key and i.get("field") not in placed]
            if keys:
                title = " · ".join(t for t in (i18n.pick(tab.get("title", ""), tab.get("titles")), i18n.pick(section.get("title", ""), section.get("titles"))) if t)
                groups.append((title, [by_key[k] for k in keys]))
                placed.update(keys)
    rest = [f for f in fields if f.key not in placed]
    if rest:
        groups.append((_("Weitere Felder") if groups else "", rest))
    return groups


def field_entities(role, viewer):
    """What the editor's section "Felder" lists: pupils and staff (with Schulverwaltung), then every table,
    each with its fields grouped like its record page and the role's rule per field."""
    from core import fieldrules, modules
    from masks import services as ms
    from tables import services as table_services

    out = []
    sources = []
    if "schulverwaltung" in modules.active():
        sources += [("pupil", _("Schüler"), ms.fields_for("pupil", user=viewer)), ("staff", _("Personal"), ms.fields_for("staff", user=viewer))]
    sources += [(f"table:{t.key}", t.display_name, table_services.fields_of(t, user=viewer)) for t in _tables()]
    for entity, title, fields in sources:
        chosen = (role.fields or {}).get(entity, {})
        fixed = fieldrules.locked(entity)
        groups = [{"title": g, "fields": [{"field": f, "rule": chosen.get(f.key, fieldrules.EDIT), "locked": f.key in fixed} for f in items]}
                  for g, items in _groups(entity, fields)]
        if entity.startswith("table:"):
            opened = ((next((t for t in _tables() if t.key == entity.removeprefix("table:")), None) or {}).rights or {}).get(role.key, "none")
        else:
            opened = (role.areas or {}).get({"pupil": "pupils", "staff": "staff"}[entity], "none")
        out.append({"entity": entity, "title": title, "groups": groups, "closed": opened not in ("read", "edit"),
                    "count": sum(1 for rule in chosen.values() if rule in (fieldrules.READ, fieldrules.HIDDEN))})
    return out


def _form_fields(request, role):
    """{entity: {key: rule}} as posted ("field__<entity>__<key>"), for the fields the viewer may see."""
    from core import fieldrules

    posted = {}
    for name, value in request.POST.items():
        if name.startswith("field__") and value in fieldrules.RULES:
            entity, _sep, key = name.removeprefix("field__").rpartition("__")
            posted.setdefault(entity, {})[key] = value
    known = {e["entity"]: {f["field"].key for g in e["groups"] for f in g["fields"]} for e in field_entities(role, request.user)}
    out = {entity: dict(rules) for entity, rules in (role.fields or {}).items()}
    for entity, rules in posted.items():
        if entity not in known:
            continue
        current = {k: v for k, v in out.get(entity, {}).items() if k not in known[entity]}  # rules of fields the viewer cannot see stay
        current.update(fieldrules.clean(entity, {k: v for k, v in rules.items() if k in known[entity]}))
        out[entity] = current
    return {entity: rules for entity, rules in out.items() if rules}


def _form_rights(request):
    """{area: level}, [extras], {table key: right} as posted by the editor."""
    levels = {a.key: request.POST.get(f"area_{a.key}", rights.NONE) for a in rights.areas() if a.has_levels}
    levels = {k: v if v in rights.LEVELS else rights.NONE for k, v in levels.items()}
    known = {e.key for a in rights.areas() for e in a.extras}
    extras = [key for key in request.POST.getlist("extra") if key in known]
    tables = {t.key: request.POST.get(f"table_{t.key}", "none") for t in _tables()}
    return levels, extras, {k: v if v in ("none", "read", "edit") else "none" for k, v in tables.items()}


def _check_grants(request, before, after):
    """Without being IT, one grants only what one may do oneself (or what the role already had)."""
    if request.user.role == Role.IT:
        return
    gained = after - before - role_capabilities(request.user)
    if gained:
        raise ValidationError(_("Sie können nur Rechte vergeben, die Sie selbst haben."))


def _nav_preview(granted, tables):
    """The sidebar a role gets: sections and items its capabilities open."""
    preview = []
    for title, items in NAV:
        shown = [str(label) for label, _url, _icon, capability in items if capability in granted]
        if shown:
            preview.append((str(title), shown))
    readable = [name for name, right in tables if right in ("read", "edit")]
    if readable:
        preview.insert(max(len(preview) - 2, 0), (str(_("Tabellen")), readable))
    return preview


def summary(levels, extras, tables=(), fields=None):
    """Short sentences of what a role may do, for the list and the editor's side column."""
    sees, edits, may = [], [], []
    for area in rights.areas():
        level = levels.get(area.key, rights.NONE)
        if area.has_levels and level == rights.EDIT:
            edits.append(str(area.title))
        elif area.has_levels and level == rights.READ:
            sees.append(str(area.title))
        for extra in area.extras:
            if extra.key in extras and (not extra.needs or level in (rights.READ, rights.EDIT)):
                may.append(str(extra.title))
    table_edit = [name for name, right in tables if right == "edit"]
    table_read = [name for name, right in tables if right == "read"]
    lines = []
    if edits or table_edit:
        lines.append(_("Bearbeitet: %(items)s") % {"items": ", ".join(edits + table_edit)})
    if sees or table_read:
        lines.append(_("Sieht nur: %(items)s") % {"items": ", ".join(sees + table_read)})
    if may:
        lines.append(_("Darf außerdem: %(items)s") % {"items": ", ".join(may)})
    rules = [rule for entity in (fields or {}).values() for rule in entity.values()]
    if rules:
        lines.append(_("Felder: %(read)s nur lesen, %(hidden)s verborgen") % {"read": rules.count("read"), "hidden": rules.count("hidden")})
    return lines or [_("Noch keine Rechte.")]


def _role_tables(role):
    return [(t.display_name, (t.rights or {}).get(role.key, "none")) for t in _tables()]


@require("admin.users")
def roles(request):
    users = {}
    for key in User.objects.values_list("role", flat=True):
        users[key] = users.get(key, 0) + 1
    rows = [{"role": r, "users": users.get(r.key, 0), "summary": summary(r.areas, r.extras, _role_tables(r), r.fields)} for r in UserRole.objects.all()]
    return render(request, "core/roles.html", {
        "rows": rows, "it_users": users.get(Role.IT, 0), "presets": [(k, v[0]) for k, v in PRESETS.items()],
        "active_nav": "settings_home",
    })


def _new_key(name):
    base = slugify(name.translate(UMLAUTS)).replace("-", "_")[:20] or "rolle"
    taken = set(UserRole.objects.values_list("key", flat=True)) | {Role.IT}
    key, n = base, 2
    while key in taken:
        key = f"{base[:20 - len(str(n)) - 1]}_{n}"
        n += 1
    return key


@require("admin.users")
@require_POST
def role_new(request):
    name = request.POST.get("name", "").strip()[:80]
    if not name:
        messages.error(request, _("Bitte einen Namen für die Rolle eingeben."))
        return redirect("roles")
    start = request.POST.get("start", "")
    levels, extras, tables = {"home": rights.READ}, [], {}
    if start.startswith("copy:"):
        source = get_object_or_404(UserRole, key=start.removeprefix("copy:"))
        levels, extras = dict(source.areas), list(source.extras)
        tables = {t.key: (t.rights or {}).get(source.key, "none") for t in _tables()}
    elif start in PRESETS:
        levels, extras = PRESETS[start][1]()
    try:
        _check_grants(request, set(), rights.capabilities(levels, extras))
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("roles")
    with transaction.atomic():
        role = UserRole.objects.create(key=_new_key(name), name=name, areas=levels, extras=extras,
                                       order=(UserRole.objects.order_by("-order").values_list("order", flat=True).first() or 0) + 10)
        _save_tables(role, tables)
    audit.log("role", f"Rolle angelegt: {name}", request=request, target=role.pk)
    return redirect("role_edit", key=role.key)


def _save_tables(role, tables):
    from tables.models import Table

    for table in Table.objects.filter(key__in=tables):
        current = dict(table.rights or {})
        if tables[table.key] in ("read", "edit"):
            current[role.key] = tables[table.key]
        else:
            current.pop(role.key, None)
        if current != (table.rights or {}):
            table.rights = current
            table.save()


def _editor_context(request, role, levels=None, extras=None, tables=None, error=""):
    levels = role.areas if levels is None else levels
    extras = role.extras if extras is None else extras
    table_rights = {t.key: (t.rights or {}).get(role.key, "none") for t in _tables()} if tables is None else tables
    sections = []
    for area in rights.areas():
        if area.key == "admin" or (area.key == "tables" and not area.has_levels):
            continue
        if not sections or sections[-1]["title"] != str(area.section):
            sections.append({"title": str(area.section), "areas": []})
        sections[-1]["areas"].append({"area": area, "level": levels.get(area.key, rights.NONE)})
    admin = next(a for a in rights.areas() if a.key == "admin")
    design = next(a for a in rights.areas() if a.key == "tables")
    granted = rights.capabilities(levels, extras)
    named_tables = [(t.display_name, table_rights.get(t.key, "none")) for t in _tables()]
    return {
        "role": role, "sections": sections, "admin": admin, "design": design, "extras": set(extras),
        "tables": [{"table": t, "right": table_rights.get(t.key, "none")} for t in _tables()],
        "summary": summary(levels, extras, named_tables, role.fields), "nav_preview": _nav_preview(granted, named_tables),
        "accounts": User.objects.filter(role=role.key).order_by("username"), "error": error,
        "other_roles": ([(Role.IT, Role.IT.label)] if request.user.role == Role.IT else [])
                       + [(r.key, r.display_name) for r in UserRole.objects.exclude(pk=role.pk)],
        "initial": json.dumps({"levels": levels, "extras": sorted(extras), "tables": table_rights, "fields": role.fields or {}}),
        "field_entities": field_entities(role, request.user),
        "active_nav": "settings_home",
    }


@require("admin.users")
def role_edit(request, key):
    role = get_object_or_404(UserRole, key=key)
    if request.method == "POST":
        levels, extras, tables = _form_rights(request)
        try:
            _check_grants(request, rights.capabilities(role.areas, role.extras), rights.capabilities(levels, extras))
            if request.user.role != Role.IT:
                from tables import services as table_services

                for table in _tables():
                    if tables[table.key] != (table.rights or {}).get(role.key, "none") and not table_services.may_edit(request.user, table):
                        raise ValidationError(_("Sie können nur Rechte vergeben, die Sie selbst haben."))
            name = request.POST.get("name", "").strip()[:80]
            if not name:
                raise ValidationError(_("Bitte einen Namen für die Rolle eingeben."))
        except ValidationError as exc:
            return render(request, "core/role_edit.html", _editor_context(request, role, levels, extras, tables, " ".join(exc.messages)))
        scope = i18n.scope_for(request, bool(role.names))
        with transaction.atomic():
            role.name, role.names = i18n.rename(role.name, role.names, name, scope)
            role.description = request.POST.get("description", "").strip()[:300]
            role.areas, role.extras = levels, extras
            role.fields = _form_fields(request, role)
            role.save()
            _save_tables(role, tables)
        audit.log("role", f"Rechte der Rolle {role.name} geändert", request=request, target=role.pk)
        messages.success(request, _("Die Rolle %(name)s wurde gespeichert.") % {"name": role.display_name})
        return redirect("role_edit", key=role.key)
    return render(request, "core/role_edit.html", _editor_context(request, role))


@require("admin.users")
@require_POST
def role_preview(request, key):
    """The side column while the editor is changed: summary and sidebar, from what is ticked now."""
    role = get_object_or_404(UserRole, key=key)
    levels, extras, tables = _form_rights(request)
    named = [(t.display_name, tables.get(t.key, "none")) for t in _tables()]
    return render(request, "core/_role_summary.html", {
        "summary": summary(levels, extras, named, _form_fields(request, role)), "nav_preview": _nav_preview(rights.capabilities(levels, extras), named), "role": role,
    })


@require("admin.users")
@require_POST
def role_delete(request, key):
    role = get_object_or_404(UserRole, key=key)
    holders = User.objects.filter(role=role.key)
    target = request.POST.get("move_to", "")
    if holders.exists():
        if target == role.key or (target != Role.IT and not UserRole.objects.filter(key=target).exists()):
            messages.error(request, _("Bitte wählen, welche Rolle die Konten stattdessen bekommen."))
            return redirect("role_edit", key=role.key)
        if target == Role.IT and request.user.role != Role.IT:
            raise PermissionDenied
    from masks.models import Mask
    from tables.models import Table

    with transaction.atomic():
        moved = holders.update(role=target) if holders.exists() else 0
        for table in Table.objects.all():
            if role.key in (table.rights or {}):
                table.rights = {k: v for k, v in table.rights.items() if k != role.key}
                table.save()
        for mask in Mask.objects.all():
            if role.key in (mask.roles or []):
                mask.roles = [r for r in mask.roles if r != role.key]
                mask.save()
        role.delete()
    audit.log("role", f"Rolle gelöscht: {role.name}" + (f", {moved} Konten zu {target}" if moved else ""), request=request)
    messages.success(request, _("Die Rolle %(name)s wurde gelöscht.") % {"name": role.display_name})
    return redirect("roles")


def last_it(user):
    """Whether `user` is the only active IT account, which must keep its role and stay unlocked."""
    return user.role == Role.IT and user.is_active and not User.objects.filter(role=Role.IT, is_active=True).exclude(pk=user.pk).exists()


def may_assign(manager, role_key):
    """Whether `manager` may give an account this role: IT may give any; others no role that can do more
    than they can (and so never IT)."""
    if manager.role == Role.IT:
        return True
    if role_key == Role.IT:
        return False
    row = UserRole.objects.filter(key=role_key).values("areas", "extras").first()
    granted = rights.capabilities(row["areas"], row["extras"]) if row else set()
    return granted <= role_capabilities(manager)


def field_rights(request, entity, key):
    """The box "Rechte" on a field's page: every role and its rule for this field (IT always edits)."""
    from core import fieldrules

    if not request.user.can("admin.users"):
        return None
    return {"entity": entity, "locked": key in fieldrules.locked(entity),
            "roles": [{"role": r, "rule": (r.fields or {}).get(entity, {}).get(key, fieldrules.EDIT)} for r in UserRole.objects.all()]}


def save_field_rights(request, entity, key):
    """Saves the box "Rechte" of a field's page (action "rights")."""
    from core import fieldrules

    if not request.user.can("admin.users") or not fieldrules.visible(request.user, entity, key):
        raise PermissionDenied
    with transaction.atomic():
        for role in UserRole.objects.all():
            rule = request.POST.get(f"rule_{role.key}", fieldrules.EDIT)
            rules = dict((role.fields or {}).get(entity, {}))
            rules.pop(key, None)
            rules.update(fieldrules.clean(entity, {key: rule}))
            fields = {**(role.fields or {}), entity: rules}
            fields = {e: r for e, r in fields.items() if r}
            if fields != (role.fields or {}):
                role.fields = fields
                role.save()
    audit.log("role", f"Rechte am Feld {key} ({entity}) geändert", request=request)
    messages.success(request, _("Die Rechte am Feld wurden gespeichert."))
