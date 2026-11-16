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
    "janitor": (_("Hausmeister"), lambda: ({"home": rights.READ, "people": rights.READ, "devices": rights.EDIT, "print": rights.READ}, [])),
    "office": (_("Büro"), lambda: ({"home": rights.READ, "people": rights.EDIT, "devices": rights.READ, "print": rights.READ,
                                    "data": rights.EDIT}, ["people.archive", "people.classes"])),
}


def _tables():
    from tables.models import Table

    return list(Table.objects.filter(archived=False))


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


def summary(levels, extras, tables=()):
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
    return lines or [_("Noch keine Rechte.")]


def _role_tables(role):
    return [(t.display_name, (t.rights or {}).get(role.key, "none")) for t in _tables()]


@require("admin.users")
def roles(request):
    users = {}
    for key in User.objects.values_list("role", flat=True):
        users[key] = users.get(key, 0) + 1
    rows = [{"role": r, "users": users.get(r.key, 0), "summary": summary(r.areas, r.extras, _role_tables(r))} for r in UserRole.objects.all()]
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
        "summary": summary(levels, extras, named_tables), "nav_preview": _nav_preview(granted, named_tables),
        "accounts": User.objects.filter(role=role.key).order_by("username"), "error": error,
        "other_roles": ([(Role.IT, Role.IT.label)] if request.user.role == Role.IT else [])
                       + [(r.key, r.display_name) for r in UserRole.objects.exclude(pk=role.pk)],
        "initial": json.dumps({"levels": levels, "extras": sorted(extras), "tables": table_rights}),
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
        "summary": summary(levels, extras, named), "nav_preview": _nav_preview(rights.capabilities(levels, extras), named), "role": role,
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
