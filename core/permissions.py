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
    # People by kind; "person.view/edit/archive" mean "of any kind" (DERIVED below).
    "pupil.view": {IT, SEK, SL},
    "pupil.edit": {IT, SEK},
    "pupil.archive": {IT, SEK},
    "staff.view": {IT, SEK, SL},
    "staff.edit": {IT, SEK},
    "staff.archive": {IT, SEK},
    "guardian.view": {IT, SEK, SL},
    "guardian.edit": {IT, SEK},
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
for _capability in ("pupil.view", "pupil.edit", "pupil.archive", "staff.view", "staff.edit", "staff.archive", "guardian.view",
                    "guardian.edit", "class.manage", "device.view", "device.edit",
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


# Capabilities that hold when one of these does: checks that do not know which kind of person.
DERIVED = {
    "person.view": ("pupil.view", "staff.view", "guardian.view"),
    "person.edit": ("pupil.edit", "staff.edit", "guardian.edit"),
    "person.archive": ("pupil.archive", "staff.archive"),
}

# "Einstellungen" opens for anyone with one of these; it is not a right of its own (core/context.py NAV).
SETTINGS = ("admin.masks", "admin.fields", "tables.design", "admin.platforms", "admin.users", "admin.school",
            "admin.update", "admin.backup", "admin.audit")


def has_capability(user, capability):
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if capability == "settings.open":
        return any(has_capability(user, c) for c in SETTINGS)
    if capability in DERIVED:
        return any(has_capability(user, c) for c in DERIVED[capability])
    module = CAPABILITY_MODULES.get(capability)
    if module and module not in active():
        return False  # a module this database does not use
    if capability in role_capabilities(user):
        return True
    check = CAPABILITY_CHECKS.get(capability)
    return bool(check and check(user))


# A database without Schulverwaltung (a club, a library) starts with these instead of Sekretariat and
# Schulleitung: one that works with the data, one that only looks.
NEUTRAL_ROLES = [
    ("team", _("Team"), {"home": "read", "print": "read", "data": "edit"}),
    ("gast", _("Gast"), {"home": "read", "print": "read", "data": "read"}),
]


def _neutral_roles(using, existing):
    """Without Schulverwaltung: Team and Gast instead of the school's roles. Sekretariat and Schulleitung,
    made by the migration before the database chose to go without, leave again while nobody has them and
    nobody changed them."""
    from .models import User, UserRole

    held = set(User.objects.using(using).values_list("role", flat=True))
    for key in (SEK, SL):
        if key in existing and key not in held:
            row = UserRole.objects.using(using).filter(key=key).first()
            if row and not row.fields and abs((row.updated_at - row.created_at).total_seconds()) < 5:
                UserRole.objects.using(using).filter(pk=row.pk).delete()  # untracked: every copy of the database does the same
                existing.discard(key)
    made = [UserRole(id=UserRole.id_for(key), key=key, name=str(label), names=_translations(label), areas=dict(levels), order=(n + 1) * 10)
            for n, (key, label, levels) in enumerate(NEUTRAL_ROLES) if key not in existing]
    if made:
        UserRole.objects.using(using).bulk_create(made, ignore_conflicts=True)
        existing.update(r.key for r in made)


def _translations(label):
    """{language: name} of a built-in role in the other languages (masks/i18n.py names)."""
    from django.conf import settings
    from django.utils import translation

    names = {}
    for language, _name in settings.LANGUAGES:
        if language != settings.LANGUAGE_CODE:
            with translation.override(language):
                names[language] = str(label)
    return {k: v for k, v in names.items() if v != str(label)}


def ensure_roles(using="default"):
    """A row for every built-in role that has none yet, with the rights it had before (after migrate).
    Not tracked: a computer makes the same rows (the ids follow from the key) and then takes the server's."""
    from django.db.utils import DatabaseError

    from . import rights
    from .models import UserRole, role_choices
    from .modules import ROLES

    try:
        existing = set(UserRole.objects.using(using).values_list("key", flat=True))
        school = "schulverwaltung" in active()
    except DatabaseError:
        return []
    builtin = [(value, label) for value, label in Role.choices if value != IT] if school else []
    builtin += [(value, label) for value, label, _module in ROLES]
    if not school:
        _neutral_roles(using, existing)
    summaries = dict(ROLE_SUMMARY) | {value: summary for value, summary, _module in ROLE_SUMMARIES}
    made = []
    for order, (value, label) in enumerate(builtin, start=1):
        if value in existing:
            continue
        levels, extras = rights.from_capabilities(default_capabilities(value))
        made.append(UserRole(id=UserRole.id_for(value), key=value, name=str(label), names=_translations(label),
                             areas=levels, extras=extras, order=order * 10))
    if made:
        UserRole.objects.using(using).bulk_create(made, ignore_conflicts=True)
    # Built-in roles made before their names were translated get the translations; the German summary they
    # were given as description goes (the summary of the role page says it in every language).
    for row in UserRole.objects.using(using).filter(key__in=[value for value, _label in builtin]):
        label = dict(builtin).get(row.key)
        update = {}
        if not row.names:
            update["names"] = _translations(label)
        if row.description and row.description == str(summaries.get(row.key, ""))[:300]:
            update["description"] = ""
        if update:
            UserRole.objects.using(using).filter(pk=row.pk).update(**update)
    # Rows that hold a module's rights as single switches get them as the module's area (rights.normalize).
    for row in UserRole.objects.using(using).filter(key__in=existing):
        if any(extra.startswith("cap:") for extra in row.extras or []):
            levels, extras = rights.normalize(row.areas, row.extras)
            if (levels, extras) != (row.areas, row.extras):
                UserRole.objects.using(using).filter(pk=row.pk).update(areas=levels, extras=extras)
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
