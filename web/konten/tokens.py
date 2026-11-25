"""Sign-in links. A link holds the address and the account's last sign-in, signed and valid for 15 minutes; once
used, the last sign-in changes and the link is spent. An address without an account gets one when its link is used,
so nobody can create accounts for addresses they do not read."""
from django.core import signing

SALT = "datafluss-web.login"
MAX_AGE = 15 * 60


def _stamp(user):
    return int(user.last_login.timestamp()) if user is not None and user.last_login else 0


def make(email, user=None):
    return signing.dumps({"e": email.lower(), "l": _stamp(user)}, salt=SALT, compress=True)


def read(token):
    """The address of a valid, unused link, or None."""
    from .models import User

    try:
        data = signing.loads(token, salt=SALT, max_age=MAX_AGE)
    except signing.BadSignature:
        return None
    email = str(data.get("e", ""))
    user = User.objects.filter(email=email).first()
    if user is not None and (not user.is_active or _stamp(user) != data.get("l")):
        return None
    if user is None and data.get("l"):
        return None
    return email
