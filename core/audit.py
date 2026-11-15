import ipaddress

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from .models import AuditEvent


LABELS = {
    "2fa_enrolled": _("Zwei-Faktor eingerichtet"), "2fa_policy": _("Zwei-Faktor-Regel"), "2fa_reset": _("Zwei-Faktor zurückgesetzt"),
    "attachment": _("Datei gelöscht"), "backup": _("Sicherung"), "bulk": _("Sammelaktion"), "card": _("Karte"), "card_lookup": _("Kartensuche"),
    "credential": _("Zugang"), "daz": _("DaZ"), "daz_pdf": _("DaZ-PDF"), "device": _("Gerät"), "download": _("Datei geöffnet"), "export": _("Export"),
    "export_secrets": _("Export mit Passwörtern"), "field": _("Feld"), "import": _("Import"), "kiga": _("Kindergarten"), "kiga_pdf": _("Kindergarten-PDF"),
    "login": _("Anmeldung"), "login_failed": _("Anmeldung fehlgeschlagen"), "logout": _("Abmeldung"), "mask": _("Maske"),
    "password_changed": _("Passwort geändert"), "password_reset": _("Passwort neu erzeugt"), "person_created": _("Person angelegt"),
    "platform": _("Plattform"), "print": _("Druck"), "reveal": _("Passwort angezeigt"), "rollover": _("Schuljahreswechsel"),
    "server_error": _("Serverfehler"), "settings": _("Einstellungen"), "teaching": _("Unterricht"), "template": _("Druckvorlage"), "update": _("Aktualisierung"),
    "user_changed": _("Benutzer geändert"), "user_created": _("Benutzer angelegt"),
}


def label(action):
    return str(LABELS.get(action, action))


def client_ip(request):
    """The proxy adds the real address last. Anything before it came from the client and proves nothing."""
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[-1].strip()
    address = forwarded or request.META.get("REMOTE_ADDR") or ""
    try:
        return str(ipaddress.ip_address(address))
    except ValueError:
        return None


def log(action, summary, request=None, user=None, target=""):
    if user is None and request is not None and request.user.is_authenticated:
        user = request.user
    return AuditEvent.objects.create(
        action=action,
        summary=summary[:300],
        user=user,
        target_id=str(target or ""),
        ip=client_ip(request),
        server_id=settings.SERVER_ID,
    )
