from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from vault import crypto


def data_is_a_volume():
    mounts = Path("/proc/self/mountinfo").read_text().splitlines()
    return any(line.split()[4] == "/data" for line in mounts)


def stored_fingerprint():
    """Reads the table directly, because this runs before the migrations and the column may be missing."""
    if "core_schoolsettings" not in connection.introspection.table_names():
        return ""
    with connection.cursor() as cursor:
        columns = [c.name for c in connection.introspection.get_table_description(cursor, "core_schoolsettings")]
        if "key_check" not in columns:
            return ""
        cursor.execute("SELECT key_check FROM core_schoolsettings LIMIT 1")
        row = cursor.fetchone()
    return row[0] if row else ""


class Command(BaseCommand):
    help = "Runs first when a container starts, so nothing touches the database on a wrong setup."

    def handle(self, *args, **options):
        # A missing key must stop the start. A new key would make every stored password unreadable.
        crypto.check_key()
        if settings.REQUIRE_DATA_VOLUME and not data_is_a_volume():
            raise CommandError("/data ist kein Volume. Fotos und Sicherungen gingen beim nächsten Update verloren.")
        stored = stored_fingerprint()
        if stored and stored != crypto.derived_password("key-check"):
            raise CommandError("VAULT_KEY passt nicht zu dieser Datenbank. Gespeicherte Passwörter wären nicht lesbar.")
