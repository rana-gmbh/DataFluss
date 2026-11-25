"""createsuperuser for the template site: an address and a name, no password. Accounts sign in by link
(konten/tokens.py) and reviewers confirm a code from an authenticator app, so a password would never be used.
An existing account becomes a reviewer."""
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email

from konten.models import User


class Command(BaseCommand):
    help = "Legt ein Prüferkonto an oder macht ein Konto zum Prüfer (E-Mail und Name, ohne Passwort)."

    def add_arguments(self, parser):
        parser.add_argument("--email", default="")
        parser.add_argument("--name", default="")
        parser.add_argument("--noinput", "--no-input", action="store_false", dest="interactive")

    def handle(self, email="", name="", interactive=True, **options):
        if interactive and not email:
            email = input("E-Mail: ")
        if interactive and not name:
            name = input("Anzeigename (leer lassen: beim ersten Anmelden wählen): ")
        email, name = email.strip().lower(), " ".join(name.split())[:60]
        try:
            validate_email(email)
        except ValidationError:
            raise CommandError("Bitte eine gültige E-Mail-Adresse angeben.")
        user = User.objects.filter(email=email).first()
        if user is None:
            User.objects.create_superuser(email, name)
            self.stdout.write(f"Prüferkonto {email} angelegt. Anmelden per Link auf der Website, dann den zweiten Faktor einrichten.")
            return
        user.is_staff = user.is_superuser = True
        if name and not user.name:
            user.name = name
        user.save(update_fields=["is_staff", "is_superuser", "name"])
        self.stdout.write(f"{email} ist jetzt Prüfer.")
