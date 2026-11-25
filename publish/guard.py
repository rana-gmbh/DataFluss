"""A password in front of a site this server hosts (docs/publish.md "Passwort"). One password per site, a
plain page to enter it, then a signed cookie for 30 days that only this site's path gets. The cookie holds the
end of the password's hash, so a new password signs everyone out. Failed tries are counted per address and
site in the log (core/audit.py) and locked for a quarter of an hour after ten."""
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.core import signing
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _

from core import audit

COOKIE_DAYS = 30
WINDOW = timedelta(minutes=15)
MAX_FAILURES = 10
SALT = "datafluss.publish.site"
FAILED = "site_password_failed"


def hashed(plain):
    return make_password(plain) if plain else ""


def cookie_name(slug):
    return f"dfsite_{slug}"


def _token(slug, password_hash):
    return [slug, password_hash[-16:]]


def allowed(request, slug, password_hash):
    """Whether this visitor may see the site: it has no password, or the visitor's cookie fits the current one."""
    if not password_hash:
        return True
    try:
        value = signing.loads(request.COOKIES.get(cookie_name(slug), ""), salt=SALT, max_age=timedelta(days=COOKIE_DAYS))
    except signing.BadSignature:
        return False
    return value == _token(slug, password_hash)


def _locked(request, slug):
    since = timezone.now() - WINDOW
    from core.models import AuditEvent

    return AuditEvent.objects.filter(action=FAILED, target_id=slug, ip=audit.client_ip(request), at__gte=since).count() >= MAX_FAILURES


def page(request, slug, password_hash, title):
    """The password page, or after the right password the way back with the cookie set."""
    error = ""
    if request.method == "POST":
        if _locked(request, slug):
            error = _("Zu viele Fehlversuche. Bitte in 15 Minuten erneut versuchen.")
        elif check_password(request.POST.get("password", ""), password_hash):
            response = redirect(request.path)
            response.set_cookie(cookie_name(slug), signing.dumps(_token(slug, password_hash), salt=SALT),
                                max_age=COOKIE_DAYS * 24 * 3600, path=f"/p/{slug}/", httponly=True, samesite="Lax",
                                secure=request.is_secure())
            return response
        else:
            audit.log(FAILED, f"Falsches Passwort für die Website {slug}", request=request, target=slug)
            error = _("Das Passwort stimmt nicht.")
    response = render(request, "publish/site_password.html", {"title": title, "error": error}, status=401 if error else 200)
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Cache-Control"] = "no-store"
    return response
