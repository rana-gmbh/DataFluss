"""One place for what each role may do. Masks never grant or remove access.

CAPABILITIES is what each built-in role could do before roles became editable: the defaults its row
(core.models.UserRole) is created with. Once a role has a row, its rights come from there (core/rights.py);
IT has no row and keeps every right.
"""
from functools import wraps

from django.core.exceptions import PermissionDenied
from django.utils.translation import gettext_lazy as _

from .models import Role
from .modules import CAPABILITY_MODULES, ROLE_SUMMARIES, active

IT, SEK, SL = Role.IT, Role.SEKRETARIAT, Role.SCHULLEITUNG

# Modules add their own capabilities and roles (core/modules.py add_capabilities).
CAPABILITIES = {
    "person.view": {IT, SEK, SL},
    "person.edit": {IT, SEK},
    "person.archive": {IT, SEK},
    "class.manage": {IT, SEK},
    "device.view": {IT, SEK, SL},
    "device.edit": {IT, SEK},
    "vault.view": {IT, SEK},
    "vault.reveal": {IT, SEK},
    "vault.edit": {IT, SEK},
    "vault.export": {IT},
    "print.run": {IT, SEK, SL},
    "print.secrets": {IT, SEK},
    "print.design": {IT},
    "print.code": {IT},
    "data.import": {IT, SEK},
    "data.export": {IT, SEK, SL},
    "admin.fields": {IT},
    "admin.masks": {IT},
    "admin.platforms": {IT},
    "admin.users": {IT},
    "admin.audit": {IT, SL},
    "admin.school": {IT},
    "admin.update": {IT},
    "admin.backup": {IT},
    "tables.design": {IT},  # own tables: tables, fields, rights; records follow each table's rights
    "home.view": {IT, SEK, SL},  # the start page; module roles have their own (core/modules.py "home")
}

# Capabilities of the basic module Schulverwaltung: a database that does not use it (SchoolSettings.modules_off)
# has no people, devices or logins, so nobody has these there (has_capability below).
for _capability in ("person.view", "person.edit", "person.archive", "class.manage", "device.view", "device.edit",
                    "vault.view", "vault.reveal", "vault.edit", "vault.export", "admin.fields", "admin.platforms"):
    CAPABILITY_MODULES.setdefault(_capability, "schulverwaltung")

ROLE_SUMMARY = [
    (IT, _("Voller Zugriff, inklusive Masken, Vorlagen, Benutzer und Export von Passwörtern.")),
    (SEK, _("Stammdaten pflegen, Passwörter anzeigen und Zugangsblätter drucken.")),
    (SL, _("Daten und Berichte lesen, keine Passwörter.")),
]


# Capabilities that also follow from a user's records, whatever the role (core/modules.py add_capability_check).
CAPABILITY_CHECKS = {}


def role_summary():
    """The roles' descriptions for the user page: the platform's and those of the modules in use."""
    using = active()
    return ROLE_SUMMARY + [(value, summary) for value, summary, module in ROLE_SUMMARIES if module in using]


def offered_roles():
    """The roles the user page offers: the platform's and those of the loaded modules."""
    from .models import role_choices

    return role_choices()


def default_capabilities(role):
    """What a built-in role could do before it had a row: the defaults its row starts with."""
    return {capability for capability, roles in CAPABILITIES.items() if role in roles}


def role_capabilities(user):
    """The capabilities of the user's role, worked out once per request (kept on the user object)."""
    cached = getattr(user, "_role_capabilities", None)
    if cached is not None and cached[0] == user.role:
        return cached[1]
    from . import rights
    from .models import UserRole

    row = UserRole.objects.filter(key=user.role).values("areas", "extras").first() if user.role != IT else None
    granted = rights.capabilities(row["areas"], row["extras"]) if row else default_capabilities(user.role)
    user._role_capabilities = (user.role, granted)
    return granted


# "Einstellungen" opens for anyone with one of these; it is not a right of its own (core/context.py NAV).
SETTINGS = ("admin.masks", "admin.fields", "tables.design", "admin.platforms", "admin.users", "admin.school",
            "admin.update", "admin.backup", "admin.audit")


def has_capability(user, capability):
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if capability == "settings.open":
        return any(has_capability(user, c) for c in SETTINGS)
    module = CAPABILITY_MODULES.get(capability)
    if module and module not in active():
        return False  # a module this database does not use
    if capability in role_capabilities(user):
        return True
    check = CAPABILITY_CHECKS.get(capability)
    return bool(check and check(user))


def ensure_roles(using="default"):
    """A row for every built-in role that has none yet, with the rights it had before (after migrate).
    Not tracked: a computer makes the same rows (the ids follow from the key) and then takes the server's."""
    from django.db.utils import DatabaseError

    from . import rights
    from .models import UserRole, role_choices
    from .modules import ROLES

    try:
        existing = set(UserRole.objects.using(using).values_list("key", flat=True))
    except DatabaseError:
        return []
    builtin = [(value, label) for value, label in Role.choices if value != IT]
    builtin += [(value, label) for value, label, _module in ROLES]
    summaries = dict(ROLE_SUMMARY) | {value: summary for value, summary, _module in ROLE_SUMMARIES}
    made = []
    for order, (value, label) in enumerate(builtin, start=1):
        if value in existing:
            continue
        levels, extras = rights.from_capabilities(default_capabilities(value))
        made.append(UserRole(id=UserRole.id_for(value), key=value, name=str(label), description=str(summaries.get(value, ""))[:300],
                             areas=levels, extras=extras, order=order * 10))
    if made:
        UserRole.objects.using(using).bulk_create(made, ignore_conflicts=True)
    return made


def require(capability):
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not has_capability(request.user, capability):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapped

    return decorator
