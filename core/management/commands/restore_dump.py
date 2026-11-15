from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from core import updates


class Command(BaseCommand):
    help = "Replaces the whole database with a dump from before an update or a school-year rollover."

    def add_arguments(self, parser):
        parser.add_argument("name", help="The file name in the backup folder")
        parser.add_argument("--confirm", action="store_true", help="Required, because all newer data is lost")

    def handle(self, *args, **options):
        name = Path(options["name"]).name
        if not (Path(settings.BACKUP_ROOT) / name).is_file():
            raise CommandError(f"{name} liegt nicht in {settings.BACKUP_ROOT}.")
        if not options["confirm"]:
            raise CommandError("Das ersetzt die ganze Datenbank. Alles, was danach gespeichert wurde, geht verloren. Zur Bestätigung --confirm angeben.")
        updates.restore_database(name)
        call_command("migrate", interactive=False, verbosity=0)  # The dump may be older than this version.
        self.stdout.write(self.style.SUCCESS(f"Datenbank aus {name} wiederhergestellt."))
