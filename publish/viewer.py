"""The reader a publication renders as (docs/publish.md "Sicherheit").

PublicationViewer looks like a signed-in user to the code that shows tables: tables/services.right and
core/fieldrules.rules ask it directly (`table_right`, `field_rules`). It reads a table only when the
publication chose it AND its role may read it, and sees a field only when the publication chose it AND the
role does not hide it. Secret fields are never seen. It has no capabilities at all, so links to pupils,
staff and devices resolve as "nicht sichtbar", and its role key is one no UserRole has, so any code path
that falls back to role lookups finds nothing.

Every function that shows records must be given this viewer: several of them treat `user=None` as a system
task that sees everything.
"""
from types import SimpleNamespace

from core import fieldrules
from tables import services
from tables.models import NONE, READ, Field, Table


class PublicationViewer:
    is_authenticated = True
    is_active = True
    is_anonymous = False
    is_staff = is_superuser = False
    pk = id = None
    username = ""

    def __init__(self, publication):
        self.publication = publication
        self.role = f"publication:{publication.pk}"  # no UserRole has this key
        self._base = SimpleNamespace(role=publication.role, is_authenticated=True)  # the bound role, for its own rights and rules
        self._chosen = publication.tables()
        self._tables = {}
        self._rules = {}

    def __str__(self):
        return self.publication.name

    def can(self, capability):
        return False

    def get_full_name(self):
        return ""

    display_name = ""

    # ---------------------------------------------------------------- tables/services.right

    def table_right(self, table):
        if table.pk not in self._tables:
            ok = table.key in self._chosen and not table.archived and services.may_read(self._base, table)
            self._tables[table.pk] = READ if ok else NONE
        return self._tables[table.pk]

    def readable_tables(self):
        return [t for t in Table.objects.filter(archived=False).order_by("order", "name") if self.table_right(t) == READ]

    # ---------------------------------------------------------------- core/fieldrules.rules

    def field_rules(self, entity):
        """Every field of the entity that is not published, as "hidden"; the rest "read" (nothing is edited)."""
        if entity not in self._rules:
            self._rules[entity] = self._compute_rules(entity)
        return self._rules[entity]

    def _compute_rules(self, entity):
        if not entity.startswith("table:"):
            return _AllHidden()
        key = entity.removeprefix("table:")
        table = Table.objects.filter(key=key).first()
        keys = list(Field.objects.filter(table__key=key).values_list("key", "type"))
        if table is None or self.table_right(table) != READ:
            return _AllHidden({k: fieldrules.HIDDEN for k, _t in keys})
        role_hidden = fieldrules.hidden(self._base, entity)
        # The title names a record in lists, links and on its page, so it always comes along (as for roles).
        chosen = set((self._chosen.get(key) or {}).get("fields") or []) | fieldrules.locked(entity)
        out = {}
        for field_key, field_type in keys:
            seen = field_key in chosen and field_key not in role_hidden and field_type != "secret"
            out[field_key] = fieldrules.READ if seen else fieldrules.HIDDEN
        return out

    def visible_keys(self, table):
        """Keys of the fields this viewer sees in `table`."""
        return {k for k, rule in self.field_rules(fieldrules.table_entity(table)).items() if rule != fieldrules.HIDDEN}


class _AllHidden(dict):
    """Rules of an entity the publication does not show: every key is hidden, even ones not listed."""

    def get(self, key, default=None):
        return super().get(key, fieldrules.HIDDEN)
