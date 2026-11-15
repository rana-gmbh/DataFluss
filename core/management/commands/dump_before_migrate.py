from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from core import updates
from core.models import UpdateState


class Command(BaseCommand):
    help = "Runs when a container starts, so a rollback loses as little as possible and a Redeploy has a backup too."

    def handle(self, *args, **options):
        executor = MigrationExecutor(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if not pending or not executor.loader.applied_migrations:
            return
        name = updates.backup_database()
        if name and UpdateState._meta.db_table in connection.introspection.table_names():
            UpdateState.objects.filter(pk=1, deployment_status__in=updates.RUNNING).update(backup_name=name, started_at=timezone.now())
        self.stdout.write(f"Datenbank vor der Änderung gesichert: {name}")
