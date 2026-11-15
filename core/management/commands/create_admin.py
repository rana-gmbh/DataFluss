import getpass

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from core.models import Role, User


class Command(BaseCommand):
    help = "Creates the first IT account on a new installation."

    def add_arguments(self, parser):
        parser.add_argument("username")

    def handle(self, *args, **options):
        username = options["username"].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise CommandError("Dieser Benutzername ist vergeben.")
        password = getpass.getpass("Passwort (mind. 12 Zeichen): ")
        if password != getpass.getpass("Passwort wiederholen: "):
            raise CommandError("Die Passwörter stimmen nicht überein.")
        try:
            validate_password(password)
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages))
        User.objects.create_user(username, password=password, role=Role.IT)
        self.stdout.write(f"{username} wurde als IT angelegt.")
