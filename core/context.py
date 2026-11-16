import os

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from . import modules
from .models import SchoolSettings

# Capabilities that open the search. Modules add theirs (core/modules.py).
SEARCHERS = ["person.view", "home.view"]
# The menu. Modules add their sections (core/modules.py add_nav_section).
NAV = [
    (_("Übersicht"), [(_("Start"), "dashboard", "home", "home.view")]),
    (
        _("Personen"),
        [
            (_("Schüler"), "pupils", "users", "pupil.view"),
            (_("Personal"), "staff", "briefcase", "staff.view"),
            (_("Eltern"), "guardians", "heart", "guardian.view"),
            (_("Klassen"), "classes", "school", "pupil.view"),
        ],
    ),
    (
        _("IT"),
        [
            (_("Geräte"), "assets", "laptop", "device.view"),
            (_("Schäden"), "cases", "wrench", "device.view"),
            (_("Zugänge"), "credentials", "key", "vault.view"),
            (_("Karte suchen"), "card_lookup", "card", "person.view"),
        ],
    ),
    (
        _("Drucken und Daten"),
        [
            (_("Druckvorlagen"), "reports", "printer", "print.run"),
            (_("Import & Export"), "transfer", "swap", "data.export"),
        ],
    ),
    (
        _("Verwaltung"),
        [
            (_("Sicherung"), "backups", "archive", "admin.backup"),
            (_("Einstellungen"), "settings_home", "settings", "settings.open"),
        ],
    ),
]


def _add_tables(nav, user):
    """Own tables the user may read, as a section "Tabellen" before "Drucken und Daten". Their items carry a
    path instead of a URL name (templates/base.html), and their pages set active_nav to that path."""
    from tables.services import nav_items

    groups = {}
    for label, url, icon, _key, section in nav_items(user):  # one sidebar section per section of the tables
        groups.setdefault(section or "", []).append((label, url, icon, ""))
    if not groups:
        return
    before = next((i for i, (title, _items) in enumerate(nav) if str(title) == str(_("Drucken und Daten"))), len(nav))
    for section, items in reversed(list(groups.items())):
        nav.insert(before, (section or _("Tabellen"), items))


def _tour(user):
    """The guided tour's steps for this user, and whether it starts by itself (first sign-in)."""
    if user is None or not user.is_authenticated:
        return {}
    from .tour import steps_for

    return {"tour_steps": steps_for(user), "tour_auto": "platform" not in (user.tours_seen or [])}


def _module_stylesheets():
    """Stylesheets of modules loaded from bundles: the platform's CSS was built without their templates."""
    from schule import bundles

    return bundles.stylesheets()


def _sync_server():
    if not os.environ.get("SYNC_TOKEN"):
        return ""
    from sync.models import ClientState

    state = ClientState.load()
    return state.server_url if state else ""


def app_context(request):
    user = getattr(request, "user", None)
    nav = []
    if user is not None and user.is_authenticated:
        using = modules.active()
        for title, items in NAV:
            if modules.NAV_MODULES.get(str(title), "platform") not in using | {"platform", None}:
                continue  # a module this database does not use
            visible = [i for i in items if user.can(i[3])]
            if visible:
                nav.append((title, visible))
        _add_tables(nav, user)
    return {
        "school": SchoolSettings.load(),
        # The name after the page title and on the sign-in page: the module's, or the platform's without it.
        "product_name": _("Schulverwaltung") if "schulverwaltung" in modules.active() else "DataFluss",
        "is_school": "schulverwaltung" in modules.active(),  # "Schule" or "Organisation" in settings
        "nav": nav,
        "can_search": bool(user is not None and user.is_authenticated and any(user.can(c) for c in SEARCHERS)),
        "oidc_enabled": settings.OIDC_ENABLED,
        "server_id": settings.SERVER_ID,
        # Set by the desktop client, which shows a switcher between its databases in the sidebar.
        "client": {"db_name": os.environ["SCHULE_CLIENT_DB_NAME"], "synced": bool(os.environ.get("SYNC_TOKEN"))}
        if os.environ.get("SCHULE_CLIENT_DB_NAME") else None,
        "sync_server": _sync_server(),
        "person_badges": modules.hooks("person_badges"),  # links beside a person's name to a module's section
        "module_stylesheets": _module_stylesheets(),
        **_tour(user),
    }
