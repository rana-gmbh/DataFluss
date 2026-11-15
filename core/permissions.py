"""One place for what each role may do. Masks never grant or remove access."""
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
}

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


def has_capability(user, capability):
    if not user or not user.is_authenticated or not user.is_active:
        return False
    module = CAPABILITY_MODULES.get(capability)
    if module and module not in active():
        return False  # a module this database does not use
    if user.role in CAPABILITIES.get(capability, set()):
        return True
    check = CAPABILITY_CHECKS.get(capability)
    return bool(check and check(user))


def require(capability):
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not has_capability(request.user, capability):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapped

    return decorator
