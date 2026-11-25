"""What the site needs before it goes public (`python manage.py check --deploy`, docs/website.md "Start").
Django's own deploy checks run as well; these are the site's settings that Django cannot know about."""
from django.conf import settings
from django.core.checks import Error, Tags, Warning, register


@register(Tags.security, deploy=True)
def site_ready(app_configs, **kwargs):
    found = []
    if not settings.SITE_URL.startswith("https://"):
        found.append(Error("SITE_URL muss mit https:// beginnen (Links in Mails, sichere Cookies).", id="web.E001"))
    if settings.DEBUG:
        found.append(Error("DEBUG ist eingeschaltet.", id="web.E002"))
    if len(settings.SECRET_KEY) < 50 or settings.SECRET_KEY.startswith(("dev-", "build-")):
        found.append(Error("SECRET_KEY ist zu kurz oder ein Platzhalter (mindestens 50 Zeichen, zufällig).", id="web.E003"))
    if not settings.EMAIL_HOST:
        found.append(Error("EMAIL_HOST fehlt: Ohne SMTP kommen keine Anmeldelinks an.", id="web.E004"))
    if "sqlite" in settings.DATABASES["default"]["ENGINE"]:
        found.append(Error("Die Datenbank ist SQLite; im Betrieb DATABASE_URL auf PostgreSQL setzen.", id="web.E005"))
    if not settings.TRUST_PROXY:
        found.append(Warning("TRUST_PROXY ist aus: Hinter Coolifys Proxy zählen die Limits dann alle Besucher als einen.", id="web.W001"))
    if not settings.REVIEW_NOTIFY:
        found.append(Warning("REVIEW_NOTIFY ist leer: Niemand erfährt per Mail von neuen Einreichungen und Meldungen.", id="web.W002"))
    return found
