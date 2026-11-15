import re
import unicodedata
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from core.models import AuditEvent

from . import generator
from .models import Credential

UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "ø": "o", "æ": "ae", "å": "a", "ł": "l", "đ": "d", "ı": "i"})


def rate_limited(request):
    since = timezone.now() - timedelta(minutes=1)
    used = AuditEvent.objects.filter(action="reveal", user=request.user, at__gte=since).count()
    return used >= settings.REVEAL_LIMIT_PER_MINUTE


def _ascii(text):
    text = text.lower().translate(UMLAUTS)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", text)


def username_for(owner, platform):
    """Prefer the school mail for Microsoft. Otherwise build a readable unique name."""
    if platform.key == "m365" and getattr(owner, "email", ""):
        return owner.email.split("@")[0]
    first = _ascii(owner.first_name.split()[0]) if owner.first_name else ""
    last = _ascii(owner.last_name)
    base = f"{first}.{last}".strip(".") or f"user{str(owner.pk)[:6]}"
    candidate, n = base, 1
    taken = {str(v) for v in Credential.objects.filter(platform=platform).values_list("values__username", flat=True)}
    while candidate in taken:
        n += 1
        candidate = f"{base}{n}"
    return candidate


def generate_login(owner, platform, rotate=False):
    """Create or refresh a login. Secrets are generated to fit the person's age."""
    is_person = owner.__class__.__name__ == "Person"
    credential = Credential.get_for(platform, person=owner if is_person else None, guardian=None if is_person else owner)
    profile = generator.profile_for(owner if is_person else None)
    for field in platform.fields:
        key = field["key"]
        if field.get("secret"):
            if rotate or not credential.has_secret(key):
                credential.set_secret(key, generator.generate(profile))
        elif key == "username" and not credential.values.get("username"):
            credential.values["username"] = username_for(owner, platform)
    credential.must_change = True
    credential.save()
    return credential
