"""What a device holds and may change of own tables (sync/scope.py ModuleScope, docs/TABLES-PLAN.md §6).

A device gets the tables its user's role may read, with their fields and records. Designing tables
needs `tables.design`; records need the edit right on their table, checked after the push, when a new
record's table is known. Secret values stay off devices without vault access.
"""
from sync.scope import ModuleScope


def _secret(value):
    return isinstance(value, dict) and set(value) == {"secret"}


class TablesScope(ModuleScope):
    def _readable(self, scope):
        from . import services

        return [t.pk for t in services.readable(scope.user, include_archived=True)]

    def read(self, scope, add):
        from .models import Field, Record, Table

        tables = self._readable(scope)
        add(Table, Table._base_manager.filter(pk__in=tables))
        add(Field, Field._base_manager.filter(table_id__in=tables))
        add(Record, Record._base_manager.filter(table_id__in=tables))

    def may_write(self, scope, model, pk, label):
        if label in ("tables.table", "tables.field"):
            return scope.user.can("tables.design")
        if label == "tables.record":
            from . import services
            from .models import Record, Table

            table_id = Record._base_manager.filter(pk=pk).values_list("table_id", flat=True).first()
            if table_id is None:  # a new record: its table is checked in allows()
                return any(services.may_edit(scope.user, t) for t in Table.objects.all())
            return services.may_edit(scope.user, Table.objects.get(pk=table_id))
        return None

    def allows(self, scope, model, pk, when):
        if model._meta.label_lower != "tables.record":
            return True
        from . import services
        from .models import Record

        record = Record._base_manager.select_related("table").filter(pk=pk).first()
        if record is None:
            return True
        return services.may_edit(scope.user, record.table) and not services.check_record(record)

    def hide(self, scope, model):
        if scope.vault or model._meta.label_lower != "tables.record":
            return {}
        return {"values": lambda value: {k: v for k, v in (value or {}).items() if not _secret(v)}}

    def ignored(self, scope, model):
        if scope.vault or model._meta.label_lower != "tables.record":
            return set()
        from .models import Field

        return {f"values.{key}" for key in Field.objects.filter(type="secret").values_list("key", flat=True)}
