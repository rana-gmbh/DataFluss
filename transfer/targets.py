"""What an import goes into (docs/TABLES-PLAN.md §7): pupils and staff (importer.py, as always) or an own table
(tables/importer.py). The views ask the target of a session for everything that depends on it."""
from django.utils.translation import gettext as _, gettext_noop

from . import importer


class PersonTarget:
    """Pupils or staff: the importer as it has always worked."""

    MATCH_KEYS = (("legacy_ninox_id", gettext_noop("Ninox-ID (aus dem Bildpfad)")), ("email", gettext_noop("E-Mail")),
                  ("name_birth", gettext_noop("Name und Geburtsdatum")))

    def __init__(self, entity):
        self.entity = entity

    @property
    def label(self):
        return _("Schüler") if self.entity == "pupil" else _("Personal")

    def may_import(self, user):
        from core import modules

        return user.can("data.import") and "schulverwaltung" in modules.active() and user.can(f"{self.entity}.edit")

    def targets(self):
        return importer.targets(self.entity)

    def auto_mapping(self, headers):
        return importer.auto_mapping(headers, self.entity)

    def match_keys(self):
        return [(key, _(label)) for key, label in self.MATCH_KEYS]

    def default_match(self):
        return "legacy_ninox_id"

    def match_label(self):
        return _("Bestehende Personen erkennen über")

    def analyse(self, session):
        return importer.analyse(session)

    def commit(self, session, user=None):
        return importer.commit(session)

    def undo_blocker(self, session):
        return importer.undo_blocker(session)

    def rollback(self, session):
        return importer.rollback(session)


def target_for(entity):
    """The target of an entity, or None for one that does not exist (any more)."""
    if entity in ("pupil", "staff"):
        return PersonTarget(entity)
    if (entity or "").startswith("table:"):
        from tables.importer import TableTarget
        from tables.models import Table

        table = Table.objects.filter(key=entity.removeprefix("table:")).first()
        return TableTarget(table) if table else None
    return None


def choices(user):
    """Where this user may import to, for the upload form: [(entity, label)]."""
    from core import modules

    people = user.can("data.import") and "schulverwaltung" in modules.active()
    out = [(kind, label) for kind, label in (("pupil", _("Schüler")), ("staff", _("Personal"))) if people and user.can(f"{kind}.edit")]
    from tables import services

    return out + [(f"table:{t.key}", _("Tabelle: %(name)s") % {"name": t.display_name}) for t in services.readable(user) if services.may_edit(user, t)]
