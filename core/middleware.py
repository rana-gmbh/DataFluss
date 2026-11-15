import logging
import os

from django.conf import settings
from django.utils import translation
from django.utils.translation import gettext as _, gettext_lazy
from django.utils.cache import patch_vary_headers
from django.core.exceptions import PermissionDenied, SuspiciousOperation
from django.http import Http404, HttpResponse, JsonResponse
from django.middleware.gzip import GZipMiddleware
from whitenoise.middleware import WhiteNoiseMiddleware
from django.shortcuts import redirect
from django.urls import resolve, reverse

from . import audit
from .context_user import set_current_user
from .models import SchoolSettings
from .views_language import allowed_languages, fallback_language

log = logging.getLogger(__name__)
SIGNED_OUT = gettext_lazy("Sie sind nicht mehr angemeldet.")
PUBLIC_NAMES = {"login", "set_language", "js_catalog", "oidc_authentication_init", "oidc_authentication_callback", "manifest", "service_worker", "healthz", "school_logo",
                # The sync API checks a device token instead of a session.
                "sync_info", "sync_register", "sync_module", "sync_pull", "sync_push", "sync_media", "sync_media_batch", "sync_whoami"}


def _refuse(request, target):
    """A script would follow a redirect to the login page and take its 200 for a successful save."""
    if request.headers.get("X-Requested-With") or request.headers.get("HX-Request"):
        return JsonResponse({"error": str(SIGNED_OUT)}, status=401)
    return redirect(target)


class CurrentUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        set_current_user(request.user)
        return self.get_response(request)


class UserLanguageMiddleware:
    """The interface language: the signed-in user's choice, else the cookie the language menu sets,
    else German, all within the languages this database allows (Settings → Sprachen). Not the browser's language, so nobody's screen changes without asking."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        allowed = allowed_languages(request)
        known = dict(allowed)
        language = getattr(request.user, "language", "") if request.user.is_authenticated else ""
        if language not in known:
            language = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME, "")
        if language not in known:
            language = fallback_language(allowed)
        translation.activate(language)
        request.LANGUAGE_CODE = language
        try:
            response = self.get_response(request)
        finally:
            translation.deactivate()
        response.headers.setdefault("Content-Language", language)
        patch_vary_headers(response, ("Cookie",))
        return response


def _is_public(request):
    path = request.path
    if path.startswith(("/" + settings.STATIC_URL.lstrip("/"), "/favicon")):
        return True
    try:
        return resolve(path).url_name in PUBLIC_NAMES
    except Exception:
        return False


class LoginRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated and not _is_public(request):
            return _refuse(request, f"{reverse('login')}?next={request.path}")
        return self.get_response(request)


class TwoFactorMiddleware:
    """After the password step the session must also pass TOTP, unless IT switched that off."""

    ALLOWED = {"login", "logout", "totp_verify", "totp_setup", "manifest", "service_worker"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and not _is_public(request):
            try:
                name = resolve(request.path).url_name
            except Exception:
                name = ""
            if name not in self.ALLOWED and not request.session.get("second_factor_ok"):
                if SchoolSettings.load().require_2fa:
                    return _refuse(request, reverse("totp_verify" if user.totp_confirmed else "totp_setup"))
            # A connected Mac cannot change passwords (the server owns them), so only the browser asks for it.
            if user.must_change_password and name not in self.ALLOWED | {"password_change"} and not os.environ.get("SYNC_TOKEN"):
                return _refuse(request, reverse("password_change"))
        return self.get_response(request)


class PageCompressionMiddleware(GZipMiddleware):
    """Pages and lists shrink to a tenth. Pictures and PDFs are compressed already."""

    def process_response(self, request, response):
        if response.streaming or response.get("Content-Type", "").split(";")[0] not in ("text/html", "application/json"):
            return response
        return super().process_response(request, response)


class BodyLimitMiddleware:
    """Stops oversized requests before anything reads them. The login page is the only form open to everyone."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # The sync API reads a body only after its device token checked out, so it may send large pushes.
        device = request.path.startswith("/sync/v1/") and request.headers.get("Authorization", "").startswith("Bearer ")
        limit = settings.MAX_BODY_BYTES if request.user.is_authenticated or device else settings.ANONYMOUS_BODY_BYTES
        try:
            size = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            size = 0
        if size > limit:
            return HttpResponse(_("Die Datei ist zu groß."), status=413, content_type="text/plain; charset=utf-8")
        return self.get_response(request)


class ErrorLogMiddleware:
    """A traceback in the container log reaches nobody, so IT sees the count on the start page."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if isinstance(exception, (Http404, PermissionDenied, SuspiciousOperation)):
            return  # Normal answers, and anyone could fill the log with them.
        try:
            match = request.resolver_match
            audit.log("server_error", f"{type(exception).__name__} bei {request.method} {match.view_name if match else request.path}")
        except Exception:
            log.exception("Could not record a server error")


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self' data:; worker-src 'self' blob:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        )
        if request.user.is_authenticated:
            # On a shared staff computer the back button must not bring personal pages back.
            response.headers.setdefault("Cache-Control", "no-store, private")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        return response


class ModuleStaticMiddleware(WhiteNoiseMiddleware):
    """WhiteNoise, plus the static files of modules loaded from bundles (schule/bundles.py). Those are
    not in the collected static files of the build, so they are served from the bundle itself."""

    def __init__(self, get_response=None, *args, **kwargs):
        super().__init__(get_response, *args, **kwargs)
        from schule import bundles

        for folder in bundles.static_folders():
            self.add_files(str(folder), prefix=self.static_prefix)


class ActiveModulesMiddleware:
    """Reads the modules this database uses once per request (core.modules.active)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from . import modules

        token = modules.start_request()
        try:
            return self.get_response(request)
        finally:
            modules.end_request(token)
