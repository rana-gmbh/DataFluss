"""Protection against floods without any outside service: rate limits per address and account, and a trap field
plus a minimum time for forms that people fill in (bots fill every field and submit at once)."""
import hashlib
import hmac
import time
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.utils import timezone

from .models import Attempt

# kind: (how many, within seconds)
LIMITS = {
    "mail_address": (5, 3600),
    "mail_ip": (20, 3600),
    "upload": (10, 86400),
    "review": (30, 86400),
    "report": (20, 86400),
}
FORM_SALT = "datafluss-web.form"
MIN_SECONDS = 3


def client_ip(request):
    if settings.TRUST_PROXY and request.META.get("HTTP_X_FORWARDED_FOR"):
        return request.META["HTTP_X_FORWARDED_FOR"].split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _key(value):
    return hmac.new(settings.SECRET_KEY.encode(), str(value).lower().encode(), hashlib.sha256).hexdigest()


def allowed(kind, value):
    """Counts the action and says whether it is within its limit."""
    count, seconds = LIMITS[kind]
    now = timezone.now()
    Attempt.objects.filter(created__lt=now - timedelta(days=1)).delete()
    key = _key(value)
    if Attempt.objects.filter(kind=kind, key=key, created__gte=now - timedelta(seconds=seconds)).count() >= count:
        return False
    Attempt.objects.create(kind=kind, key=key, created=now)
    return True


def form_stamp():
    """The hidden value a form carries: when it was shown."""
    return signing.dumps(int(time.time()), salt=FORM_SALT)


def looks_human(request):
    """False for a form with the trap field filled in, sent too quickly, or without a valid stamp."""
    if request.POST.get("website"):
        return False
    try:
        shown = signing.loads(request.POST.get("stamp", ""), salt=FORM_SALT, max_age=24 * 3600)
    except signing.BadSignature:
        return False
    return time.time() - shown >= MIN_SECONDS
