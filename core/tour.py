"""The guided tour shown at a user's first sign-in (static/js/tour.js), and again from Hilfe or the "?" button.

Each step points at an element with data-tour="<anchor>" in the page; steps whose element is missing or
hidden (a narrow window, a role without that section) are left out. Modules add their steps through the
hook "tour" (core/modules.py), placed after the step named in "after".
"""
from django.utils.translation import gettext_lazy as _

from . import modules

STEPS = [
    {"anchor": "nav", "title": _("Bereiche"),
     "text": _("Links stehen alle Bereiche, die Ihre Rolle öffnen darf. Was Sie nicht sehen dürfen, erscheint gar nicht erst.")},
    {"anchor": "search", "title": _("Suche"),
     "text": _("Personen, Klassen, Geräte und mehr finden: hier klicken oder ⌘K (unter Windows Strg+K) drücken.")},
    {"anchor": "nav-pupils", "capability": "person.view", "title": _("Listen"),
     "text": _("In den Listen ändert ein Doppelklick eine Zelle. Spalten, Filter und Sortierung lassen sich als eigene Ansicht speichern.")},
    {"anchor": "nav-tables", "title": _("Eigene Tabellen"),
     "text": _("Tabellen, die Ihre Schule selbst gestaltet: mit eigenen Feldern, Verknüpfungen und Berechnungen. Listen und Datensätze funktionieren wie bei den Personen.")},
    {"anchor": "nav-reports", "capability": "print.run", "title": _("Drucken"),
     "text": _("Druckvorlagen für Ausweise, Listen und Briefe, mit einem Designer zum Anpassen.")},
    {"anchor": "nav-settings_home", "capability": "admin.audit", "title": _("Einstellungen"),
     "text": _("Masken, eigene Felder und Tabellen, Benutzer, Sicherung und Synchronisation.")},
    {"anchor": "database", "title": _("Datenbanken"),
     "text": _("Hier wechseln Sie zwischen den Datenbanken auf diesem Computer.")},
    {"anchor": "sync", "title": _("Abgleich"),
     "text": _("Zeigt, ob der Abgleich mit dem Server läuft. Änderungen ohne Verbindung gehen nicht verloren, sie folgen später.")},
    {"anchor": "account", "title": _("Ihr Konto"),
     "text": _("Sprache, hell oder dunkel, Passwort und Abmelden. Mit dem Fragezeichen startet dieser Rundgang wieder.")},
    {"anchor": "checklist", "title": _("Erste Schritte"),
     "text": _("Diese Liste führt durch die Einrichtung und hakt sich selbst ab.")},
]


def steps_for(user):
    """The steps this user may see, the modules' included, as plain strings for the page."""
    steps = list(STEPS)
    for extra in modules.hooks("tour"):
        for step in extra:
            after = next((i for i, s in enumerate(steps) if s["anchor"] == step.get("after")), len(steps) - 1)
            steps.insert(after + 1, step)
    if not _has_tables(user):
        steps = [s for s in steps if s["anchor"] != "nav-tables"]
    return [{"anchor": s["anchor"], "title": str(s["title"]), "text": str(s["text"])}
            for s in steps if not s.get("capability") or user.can(s["capability"])]


def _has_tables(user):
    """Own tables in this user's sidebar (tables/services.py nav_items): only then is there something to show."""
    from tables.services import nav_items

    return bool(nav_items(user))
