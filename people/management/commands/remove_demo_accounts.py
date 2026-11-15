from django.core.management.base import BaseCommand, CommandError

from core import audit
from core.models import Role, User

from .seed_demo import demo_accounts


class Command(BaseCommand):
    help = "Locks the demo accounts before real data goes in. Their names stay, because the logs point to them."

    def handle(self, *args, **options):
        demo = User.objects.filter(username__in=demo_accounts(), is_active=True)
        if not User.objects.filter(role=Role.IT, is_active=True).exclude(pk__in=demo).exists():
            raise CommandError("Es gibt noch kein anderes aktives IT-Konto. Zuerst eines mit create_admin anlegen.")
        for user in demo:
            user.is_active = False
            user.set_unusable_password()
            user.totp_secret, user.totp_confirmed = "", False
            user.save()
            audit.log("user_changed", f"Demokonto gesperrt: {user.username}")
            self.stdout.write(f"{user.username} gesperrt.")
