"""The language menu, the remembered answer of the rename dialog, and the texts for the scripts."""

import json

from django.conf import settings
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect
from django.utils import translation
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST
from django.views.i18n import JavaScriptCatalog

from .permissions import require

SCOPES = {"", "this", "all"}


def allowed_languages(request=None):
    """The (code, name) pairs people may pick in this database, in the order of settings.LANGUAGES.

    Settings → Sprachen can narrow them down; nothing chosen there means all of them."""
    if request is not None and hasattr(request, "allowed_languages"):
        return request.allowed_languages
    from core.models import SchoolSettings

    chosen = set(SchoolSettings.load().languages or [])
    allowed = [(code, name) for code, name in settings.LANGUAGES if code in chosen] or list(settings.LANGUAGES)
    if request is not None:
        request.allowed_languages = allowed
    return allowed


def fallback_language(allowed):
    """For someone who picked no language, or one that is no longer allowed: German, else the first allowed."""
    codes = [code for code, _ in allowed]
    return settings.LANGUAGE_CODE if settings.LANGUAGE_CODE in codes else codes[0]


def is_allowed(request, language):
    return language in dict(allowed_languages(request))


def _back(request):
    target = request.POST.get("next") or request.META.get("HTTP_REFERER") or "/"
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        target = "/"
    return target


@require_POST
def set_language(request):
    language = request.POST.get("language", "")
    if not is_allowed(request, language):
        return HttpResponseBadRequest()
    if request.user.is_authenticated and request.user.language != language:
        request.user.language = language
        request.user.save(update_fields=["language"])
    response = redirect(_back(request))
    response.set_cookie(settings.LANGUAGE_COOKIE_NAME, language, max_age=settings.LANGUAGE_COOKIE_AGE,
                        samesite=settings.LANGUAGE_COOKIE_SAMESITE, secure=request.is_secure(), httponly=False)
    return response


@require_POST
def name_scope(request):
    """What the rename dialog should do from now on without asking: "this", "all", or "" to ask again."""
    try:
        scope = json.loads(request.body or b"{}").get("scope", "")
    except (ValueError, AttributeError):
        scope = None
    if scope is None and "scope" in request.POST:
        scope = request.POST["scope"]
    if scope not in SCOPES:
        return HttpResponseBadRequest()
    request.user.name_scope = scope
    request.user.save(update_fields=["name_scope"])
    if request.headers.get("X-Requested-With") == "fetch" or request.content_type == "application/json":
        return JsonResponse({"ok": True, "scope": scope})
    return redirect(_back(request))


def adopt_cookie_language(request, user):
    """At sign-in: a language picked on the sign-in page becomes the user's own, if they had none yet."""
    language = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME, "")
    if not user.language and is_allowed(request, language) and language != settings.LANGUAGE_CODE:
        user.language = language
        user.save(update_fields=["language"])


class Catalog(JavaScriptCatalog):
    """gettext() for the scripts. The URL carries the language and version, so browsers may keep it."""

    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)
        versioned = request.GET.get("v") not in (None, "", "dev")  # a development server keeps "dev"
        response["Cache-Control"] = "public, max-age=31536000, immutable" if versioned else "no-cache"
        return response


js_catalog = Catalog.as_view(domain="djangojs")


def current_language():
    return translation.get_language() or settings.LANGUAGE_CODE


def language_context(request):
    user = getattr(request, "user", None)
    signed_in = bool(user is not None and user.is_authenticated)
    allowed = allowed_languages(request)
    return {
        # ui_ names, so a page's own "languages" (spoken by parents, say) does not replace them
        "ui_languages": allowed,
        "ui_single_language": len(allowed) == 1,
        "ui_language": current_language(),
        "ui_language_name": dict(settings.LANGUAGES).get(current_language(), ""),
        "ui_name_scope": user.name_scope if signed_in else "",
        "app_version": settings.APP_VERSION,
    }


def language_settings(request):
    """Konto → Sprache: the interface language and what renaming does without asking."""
    from django.contrib import messages
    from django.shortcuts import render
    from django.utils.translation import gettext as _

    if request.method == "POST":
        language = request.POST.get("language", "")
        scope = request.POST.get("name_scope", "")
        if is_allowed(request, language) and scope in SCOPES:
            request.user.language = language
            request.user.name_scope = scope
            request.user.save(update_fields=["language", "name_scope"])
            translation.activate(language)
            messages.success(request, _("Gespeichert."))
            response = redirect("language_settings")
            response.set_cookie(settings.LANGUAGE_COOKIE_NAME, language, max_age=settings.LANGUAGE_COOKIE_AGE,
                                samesite=settings.LANGUAGE_COOKIE_SAMESITE, secure=request.is_secure())
            return response
        return HttpResponseBadRequest()
    return render(request, "core/language_settings.html", {"current_scope": request.user.name_scope})


@require("admin.school")
@require_http_methods(["GET", "POST"])
def school_languages(request):
    """Settings → Sprachen: which interface languages this database offers. Checking all of them, or none,
    leaves the choice open, so languages added in a later version show up too."""
    from django.contrib import messages
    from django.shortcuts import render
    from django.utils.translation import gettext as _

    from core import audit
    from core.models import SchoolSettings

    school = SchoolSettings.load()
    if request.method == "POST":
        known = [code for code, _name in settings.LANGUAGES]
        chosen = [code for code in known if code in request.POST.getlist("languages")]
        if not chosen:
            messages.error(request, _("Wählen Sie mindestens eine Sprache."))
            return redirect("school_languages")
        school.languages = [] if len(chosen) == len(known) else chosen
        school.save(update_fields=["languages"])
        audit.log("settings", "Sprachen geändert: " + (", ".join(school.languages) or "alle"), request=request)
        messages.success(request, _("Gespeichert."))
        return redirect("school_languages")
    chosen = set(school.languages or [code for code, _name in settings.LANGUAGES])
    return render(request, "core/school_languages.html", {
        "all_languages": [(code, name, code in chosen) for code, name in settings.LANGUAGES],
        "active_nav": "settings_home",
    })
