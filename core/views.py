import base64
import hashlib
import io
import json
import mimetypes
import os
import secrets
import tempfile
from datetime import timedelta
from pathlib import Path, PurePosixPath

import pyotp
import segno
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import default_storage
from django.core.paginator import Paginator
from django.db import DatabaseError, connection
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from vault import crypto

from .views_language import adopt_cookie_language
from . import audit, health, modules, permissions, updates, views_roles
from .context import SEARCHERS
from .images import image_extension, too_large
from .models import AuditEvent, ChangeLog, Role, SchoolSettings, User, UserRole
from .permissions import offered_roles, require

# One attacker cannot lock an account for everyone else, and spreading guesses over accounts does not help.
MAX_FAILURES = 8
MAX_ACCOUNT_FAILURES = 40
MAX_ADDRESS_FAILURES = 50
WINDOW = timedelta(minutes=15)
INLINE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


def _login_key(username):
    """A hash, because a password typed into the name field must not end up in the log."""
    return hashlib.sha256(username.strip().lower().encode()).hexdigest()[:32]


def _too_many_failures(username, request):
    recent = AuditEvent.objects.filter(action="login_failed", at__gte=timezone.now() - WINDOW)
    address = audit.client_ip(request)
    account = recent.filter(target_id=_login_key(username))
    if account.count() >= MAX_ACCOUNT_FAILURES or account.filter(ip=address).count() >= MAX_FAILURES:
        return True
    return recent.filter(ip=address).count() >= MAX_ADDRESS_FAILURES


def _note_failure(request, summary, username):
    audit.log("login_failed", summary, request=request, target=_login_key(username))


def _code_is_good(user, secret, code):
    """A code counts once, so one seen over a shoulder is worthless."""
    totp = pyotp.TOTP(secret)
    now = timezone.now()
    for offset in (-1, 0, 1):
        moment = now + timedelta(seconds=offset * totp.interval)
        if secrets.compare_digest(code.encode(), totp.at(moment).encode()):
            step = totp.timecode(moment)
            return bool(User.objects.filter(pk=user.pk, totp_last_step__lt=step).update(totp_last_step=step))
    return False


def _safe_next(request):
    target = request.POST.get("next") or request.GET.get("next") or ""
    if url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return target
    return "/"


def _mac_only_owner(username):
    """On a Mac connected to a server, other accounts are names only. Says so instead of "wrong password"."""
    if not os.environ.get("SYNC_TOKEN"):
        return ""
    other = User.objects.filter(username=username).first()
    if other is None or other.has_usable_password():
        return ""
    owners = ", ".join(User.objects.filter(is_active=True).exclude(password__startswith="!").order_by("username").values_list("username", flat=True))
    return _("Auf diesem Mac kann sich nur das Konto anmelden, mit dem diese Datenbank verbunden wurde (%(owners)s). "
             "Für %(name)s bitte auf dem Startbildschirm mit „Mit Server verbinden“ eine eigene Datenbank anlegen.") % {
        "owners": owners or "–", "name": username}


@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    error = ""
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        if _too_many_failures(username, request):
            error = _("Zu viele Fehlversuche. Bitte in 15 Minuten erneut versuchen.")
        else:
            user = authenticate(request, username=username, password=request.POST.get("password", ""))
            if user is None:
                known = username if User.objects.filter(username=username).exists() else "unbekannter Benutzername"
                _note_failure(request, f"Fehlgeschlagene Anmeldung: {known}", username)
                error = _mac_only_owner(username) or _("Benutzername oder Passwort ist falsch.")
            else:
                auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
                adopt_cookie_language(request, user)
                request.session.cycle_key()
                audit.log("login", f"Anmeldung: {user.username}", request=request, user=user)
                return redirect(_safe_next(request))
    return render(request, "core/login.html", {"error": error, "next": request.GET.get("next", "")})


@require_POST
def logout_view(request):
    audit.log("logout", f"Abmeldung: {request.user.username}", request=request)
    auth_logout(request)
    return redirect("login")


@require_POST
def tour_seen(request):
    """The guided tour (core/tour.py) was finished or closed: it does not start by itself again."""
    user, name = request.user, request.POST.get("tour", "platform")
    if name in ("platform", "checklist") and name not in user.tours_seen:  # "checklist": the start page's first steps put away
        user.tours_seen = [*user.tours_seen, name]
        User.objects.filter(pk=user.pk).update(tours_seen=user.tours_seen)  # not a change to sync or log
    return JsonResponse({"ok": True})


@require_GET
def session_check(request):
    """Reaches the view only with a valid session. Long forms ask this before they send anything."""
    return HttpResponse(status=204)


def _new_password_problem(request, user, current, new, repeat):
    if not user.check_password(current):
        _note_failure(request, f"Falsches Passwort beim Ändern: {user.username}", user.username)
        return _("Das bisherige Passwort stimmt nicht.")
    if new != repeat:
        return _("Die beiden neuen Passwörter stimmen nicht überein.")
    if new == current:
        return _("Das neue Passwort muss sich vom bisherigen unterscheiden.")
    try:
        validate_password(new, user)
    except ValidationError as exc:
        return " ".join(exc.messages)
    return ""


@require_http_methods(["GET", "POST"])
def password_change(request):
    user = request.user
    if not user.has_usable_password():
        return redirect("dashboard")
    error = ""
    if request.method == "POST":
        if _too_many_failures(user.username, request):
            error = _("Zu viele Fehlversuche. Bitte in 15 Minuten erneut versuchen.")
        else:
            new = request.POST.get("new", "")
            error = _new_password_problem(request, user, request.POST.get("current", ""), new, request.POST.get("repeat", ""))
        if not error:
            user.set_password(new)
            user.must_change_password = False
            user.save(update_fields=["password", "must_change_password"])
            update_session_auth_hash(request, user)
            audit.log("password_changed", f"Passwort geändert: {user.username}", request=request)
            messages.success(request, _("Ihr Passwort wurde geändert."))
            return redirect("dashboard")
    return render(request, "core/password_change.html", {"error": error})


def _qr_data_uri(text):
    buffer = io.BytesIO()
    segno.make(text, error="m").save(buffer, kind="svg", scale=5, border=1)
    return "data:image/svg+xml;base64," + base64.b64encode(buffer.getvalue()).decode()


def _totp_context(user):
    return f"totp:{user.pk}"


@require_http_methods(["GET", "POST"])
def totp_setup(request):
    user = request.user
    if user.totp_confirmed:  # The secret is shown only once, while it is being set up.
        return redirect("dashboard" if request.session.get("second_factor_ok") else "totp_verify")
    if not user.totp_secret:
        user.totp_secret = crypto.encrypt(pyotp.random_base32(), _totp_context(user))
        user.save(update_fields=["totp_secret"])
    secret = crypto.decrypt(user.totp_secret, _totp_context(user))
    totp = pyotp.TOTP(secret)
    error = ""
    if request.method == "POST":
        if _code_is_good(user, secret, request.POST.get("code", "").replace(" ", "")):
            user.totp_confirmed = True
            user.save(update_fields=["totp_confirmed"])
            request.session["second_factor_ok"] = True
            request.session.cycle_key()
            audit.log("2fa_enrolled", f"Zwei-Faktor eingerichtet: {user.username}", request=request)
            return redirect("dashboard")
        error = _("Der Code stimmt nicht. Bitte erneut versuchen.")
    uri = totp.provisioning_uri(name=user.username, issuer_name=SchoolSettings.load().short_name)
    return render(request, "core/totp_setup.html", {"qr": _qr_data_uri(uri), "secret": secret, "error": error})


@require_http_methods(["GET", "POST"])
def totp_verify(request):
    user = request.user
    if not user.totp_confirmed or request.session.get("second_factor_ok"):
        return redirect("dashboard")
    error = ""
    if request.method == "POST":
        secret = crypto.decrypt(user.totp_secret, _totp_context(user))
        code = request.POST.get("code", "").replace(" ", "")
        if not _too_many_failures(user.username, request) and _code_is_good(user, secret, code):
            request.session["second_factor_ok"] = True
            request.session.cycle_key()
            return redirect("dashboard")
        _note_failure(request, f"Falscher Zwei-Faktor-Code: {user.username}", user.username)
        error = _("Der Code stimmt nicht.")
    return render(request, "core/totp_verify.html", {"error": error})


def dashboard(request):
    from devices.models import Assignment, SupportCase
    from people.models import Person

    if updates.is_running():
        updates.refresh_deployment()  # In case the worker that followed the update is gone.
    if "schulverwaltung" not in modules.active() and request.user.can("home.view"):
        return _tables_home(request)  # a database of own tables only
    if not request.user.can("person.view"):
        for home in modules.hooks("home"):  # a module's own start page, e.g. DaZ for a DaZ teacher
            response = home(request)
            if response is not None:
                return response
        if request.user.can("home.view"):
            return _tables_home(request, areas=True)  # a role without people: its areas and tables
        raise PermissionDenied
    pupils = Person.objects.filter(kind="pupil", archived=False)
    staff = Person.objects.filter(kind="staff", archived=False)
    context = {
        "pupil_count": pupils.count(),
        "staff_count": staff.count(),
        "device_count": Assignment.objects.filter(returned_at__isnull=True).count(),
        "open_cases": SupportCase.objects.exclude(status="Erledigt").count(),
        "update_available": request.user.can("admin.update") and updates.update_available(background_check=True),
        "warnings": health.warnings_for(request.user),
        "new_pupils": pupils.exclude(entry_date=None).select_related("current_class").order_by("-entry_date", "last_name")[:7],
        "new_staff": staff.exclude(entry_date=None).order_by("-entry_date", "last_name")[:7],
        "todo": [
            (_("Schüler ohne E-Mail"), pupils.filter(email="").exclude(school_type__in=["Kiga"]).count(), "/schueler/?preset=ohne-email"),
            (_("Ohne Foto"), pupils.filter(photo="").count(), "/schueler/?preset=ohne-foto"),
        ] if request.user.can("pupil.view") else [],
    }
    context.update(_checklist(request, pupils.count() + staff.count()))
    if request.user.can("device.edit") and request.user.can("pupil.view"):
        context["todo"] += [
            (_("Neue Codes nötig"), pupils.filter(new_codes_needed=True).count(), "/schueler/?preset=neue-codes"),
            (
                _("Ehemalige mit offenem Gerät"),
                Assignment.objects.filter(returned_at__isnull=True, person__archived=True).count(),
                "/geraete/?preset=ehemalige",
            ),
        ]
    return render(request, "core/dashboard.html", context)


def _tables_home(request, areas=False):
    """The start page of a database without Schulverwaltung: its tables, warnings and first steps."""
    from tables import services
    from tables.models import Record

    readable = services.readable(request.user)
    counts = dict(Record.objects.filter(table__in=readable).values("table_id").order_by().annotate(n=Count("id")).values_list("table_id", "n"))
    context = {
        "tables": [{"table": t, "records": counts.get(t.pk, 0)} for t in readable], "areas": areas,
        "update_available": request.user.can("admin.update") and updates.update_available(background_check=True),
        "warnings": health.warnings_for(request.user),
    }
    context.update(_checklist(request, 0))
    return render(request, "core/tables_home.html", context)


def _checklist(request, people):
    """First steps on the start page: each item ticks itself off; hidden when all are done or put away."""
    from archive import nightly, plan

    user = request.user
    if "checklist" in (user.tours_seen or []) or not (user.can("admin.school") or user.can("data.import") or user.can("tables.design")):
        return {}
    school = SchoolSettings.load()
    items = []
    if user.can("admin.school"):
        items.append((_("Name und Logo eintragen"), reverse("school_settings"), bool(school.logo) or school.name != "Meine Schule"))
    if "schulverwaltung" not in modules.active():
        from tables.models import Table

        if user.can("tables.design"):
            items.append((_("Erste eigene Tabelle anlegen"), reverse("table_list"), Table.objects.exists()))
    elif user.can("data.import"):
        items.append((_("Personen aufnehmen oder aus einer Datei importieren"), reverse("transfer"), people > 0))
    if user.can("admin.backup") and not plan.is_client():
        items.append((_("Nächtliche Sicherung einrichten"), reverse("backups"), nightly.is_set_up()))
    elif user.can("admin.backup") and plan.applies():
        items.append((_("Regelmäßige Sicherung einrichten"), reverse("backups"), plan.is_set_up()))
    if user.can("admin.users"):
        items.append((_("Konten für weitere Personen anlegen"), reverse("users"), User.objects.filter(is_active=True).count() > 1))
    done = sum(1 for _label, _url, ticked in items if ticked)
    if not items or done == len(items):
        return {}
    return {"checklist": items, "checklist_done": done}


def people_hits(queryset, terms, prefix=""):
    for term in terms:
        queryset = queryset.filter(
            Q(**{f"{prefix}first_name__icontains": term}) | Q(**{f"{prefix}last_name__icontains": term}) | Q(**{f"{prefix}email__icontains": term})
        )
    return queryset


def global_search(request):
    from devices.models import Asset, SupportCase
    from people.models import ClassGroup, Guardian, Person

    user = request.user
    if not any(user.can(capability) for capability in SEARCHERS):
        raise PermissionDenied
    query = request.GET.get("q", "").strip()
    results = []
    if len(query) >= 2:
        terms = query.split()
        from people import access

        if access.kinds(user):
            from core import fieldrules

            people = Person.objects.filter(kind__in=access.kinds(user)).select_related("current_class")
            mail_kinds = [kind for kind in access.kinds(user) if fieldrules.visible(user, kind, "email")]  # a hidden e-mail finds nobody
            for term in terms:
                people = people.filter(
                    Q(first_name__icontains=term) | Q(last_name__icontains=term) | Q(email__icontains=term, kind__in=mail_kinds) | Q(abbreviation__iexact=term)
                )
            for p in people[:8]:
                sub = (p.current_class.name if p.current_class else p.school_type) if p.kind == "pupil" else p.category
                if p.archived:
                    sub = f"{sub} · {_('archiviert')}".strip(" ·")
                results.append({"title": p.display_name, "sub": sub or "", "url": f"/personen/{p.pk}/", "icon": "user"})
            for group in ClassGroup.objects.filter(name__icontains=query).select_related("school_year").order_by("-school_year__start")[:4] \
                    if user.can("pupil.view") else []:
                results.append({"title": group.name, "sub": _("Klasse %(year)s") % {"year": group.school_year or ""}, "url": reverse("class_detail", args=[group.pk]), "icon": "school"})
        if user.can("guardian.view"):
            for g in people_hits(Guardian.objects.all(), terms)[:4]:
                results.append({"title": g.display_name, "sub": _("Eltern"), "url": f"/eltern/{g.pk}/", "icon": "heart"})
        if user.can("device.view"):
            for a in Asset.objects.filter(Q(number__icontains=query) | Q(serial__icontains=query))[:5]:
                results.append({"title": a.number, "sub": a.serial, "url": f"/geraete/{a.pk}/", "icon": "laptop"})
            for case in SupportCase.objects.filter(Q(asset__number__icontains=query) | Q(person__last_name__icontains=query)).select_related("asset", "person")[:4]:
                results.append({"title": _("Schaden %(date)s") % {"date": f"{case.date:%d.%m.%Y}"}, "sub": str(case.asset or case.person or ""), "url": reverse("case_detail", args=[case.pk]), "icon": "wrench"})
        for search in modules.hooks("search"):  # staff of a module's section find their own records
            results += search(user, terms)
    return render(request, "core/_search_results.html", {"results": results, "query": query})


@require("settings.open")
def settings_home(request):
    if [c for c in permissions.SETTINGS if request.user.can(c)] == ["admin.audit"]:
        return redirect("audit_log")  # Protokoll is all there is for this role.
    new_version = request.user.can("admin.update") and updates.update_available()
    from sync.models import Conflict

    return render(request, "core/settings_home.html", {"basic": "schulverwaltung" in modules.active(), "update_available": new_version, "sync_conflicts": Conflict.objects.filter(resolved_at=None).count(),
                                                       "datafluss_version": settings.DATAFLUSS_VERSION})


@require("admin.audit")
def audit_log(request):
    events = AuditEvent.objects.select_related("user")
    action = request.GET.get("aktion", "")
    if action:
        events = events.filter(action=action)
    page = Paginator(events, 50).get_page(request.GET.get("seite"))
    actions = AuditEvent.objects.order_by().values_list("action", flat=True).distinct()
    choices = sorted(((key, audit.label(key)) for key in actions), key=lambda row: row[1])
    for event in page:
        event.label = audit.label(event.action)
    return render(request, "core/audit.html", {"page": page, "actions": choices, "action": action, "active_nav": "settings_home"})


def _users_page(request, error="", temporary=None):
    return render(
        request, "core/users.html",
        {
            "users": User.objects.order_by("username"), "roles": offered_roles(), "error": error, "temporary": temporary,
            "role_rows": [{"role": r, "line": r.description or views_roles.summary(r.areas, r.extras)[0]} for r in UserRole.objects.all()],
            "two_factor_on": SchoolSettings.load().require_2fa,
        },
    )


@require("admin.users")
@require_http_methods(["GET", "POST"])
def users(request):
    error = ""
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        role = request.POST.get("role", Role.SCHULLEITUNG)
        try:
            if not username:
                raise ValidationError(_("Bitte einen Benutzernamen eingeben."))
            if User.objects.filter(username__iexact=username).exists():
                raise ValidationError(_("Dieser Benutzername ist vergeben."))
            if role not in dict(offered_roles()):
                raise ValidationError(_("Unbekannte Rolle."))
            if not views_roles.may_assign(request.user, role):
                raise ValidationError(_("Sie können nur Rollen vergeben, die nicht mehr dürfen als Ihre eigene."))
            if password:
                validate_password(password)
            user = User(
                username=username, role=role, email=request.POST.get("email", "").strip().lower(),
                first_name=request.POST.get("first_name", "").strip(), last_name=request.POST.get("last_name", "").strip(),
            )
            user.set_password(password) if password else user.set_unusable_password()
            user.must_change_password = bool(password)  # IT should not keep knowing it.
            user.save()
            audit.log("user_created", f"Benutzer angelegt: {username} ({role})", request=request, target=user.pk)
            messages.success(request, _("%(name)s wurde angelegt.") % {"name": username})
            return redirect("users")
        except ValidationError as exc:
            error = " ".join(exc.messages)
    return _users_page(request, error)


@require("admin.users")
@require_POST
def two_factor_switch(request):
    school = SchoolSettings.load()
    school.require_2fa = request.POST.get("action") == "on"
    school.save(update_fields=["require_2fa"])
    state = "eingeschaltet" if school.require_2fa else "ausgeschaltet"
    audit.log("2fa_policy", f"Zwei-Faktor-Anmeldung {state}", request=request)
    messages.success(request, _("Zwei-Faktor-Anmeldung eingeschaltet.") if school.require_2fa else _("Zwei-Faktor-Anmeldung ausgeschaltet."))
    return redirect("users")


@require("admin.users")
@require_POST
def user_update(request, pk):
    user = get_object_or_404(User, pk=pk)
    action = request.POST.get("action")
    if not views_roles.may_assign(request.user, user.role):
        # An account that may do more than the manager (an IT account, say) stays as it is.
        messages.error(request, _("Dieses Konto darf mehr als Ihres; ändern kann es nur IT."))
        return redirect("users")
    if action == "role" and request.POST.get("role") in dict(offered_roles()):
        chosen = request.POST["role"]
        if views_roles.last_it(user) and chosen != Role.IT:
            messages.error(request, _("Das letzte IT-Konto behält seine Rolle, sonst könnte niemand mehr alles verwalten."))
        elif (user == request.user and request.user.role != Role.IT) or not views_roles.may_assign(request.user, chosen):
            messages.error(request, _("Sie können nur Rollen vergeben, die nicht mehr dürfen als Ihre eigene."))
        else:
            user.role = chosen
            user.save(update_fields=["role"])
            audit.log("user_changed", f"Rolle von {user.username}: {user.role}", request=request, target=user.pk)
    elif action == "toggle" and user != request.user and views_roles.last_it(user):
        messages.error(request, _("Das letzte IT-Konto lässt sich nicht sperren."))
    elif action == "toggle" and user != request.user:
        user.is_active = not user.is_active
        user.save(update_fields=["is_active"])
        audit.log("user_changed", f"{user.username} {'aktiviert' if user.is_active else 'deaktiviert'}", request=request, target=user.pk)
    elif action == "reset2fa":
        user.totp_secret, user.totp_confirmed = "", False
        user.save(update_fields=["totp_secret", "totp_confirmed"])
        audit.log("2fa_reset", f"Zwei-Faktor zurückgesetzt: {user.username}", request=request, target=user.pk)
    elif action == "password" and user != request.user and user.has_usable_password():
        password = secrets.token_urlsafe(12)
        user.set_password(password)  # Also ends all of this user's sessions.
        user.must_change_password = True
        user.save(update_fields=["password", "must_change_password"])
        audit.log("password_reset", f"Neues Passwort erzeugt für {user.username}", request=request, target=user.pk)
        return _users_page(request, temporary={"username": user.username, "password": password})
    return redirect("users")


@require("admin.school")
@require_http_methods(["GET", "POST"])
def school_settings(request):
    school = SchoolSettings.load()
    if request.method == "POST":
        school.name = request.POST.get("name", school.name).strip() or school.name
        school.short_name = request.POST.get("short_name", school.short_name).strip() or school.short_name
        school.confidential_footer = request.POST.get("confidential_footer", "").strip()
        school.email_domain = request.POST.get("email_domain", "").strip().lower()
        upload = request.FILES.get("logo")
        extension = image_extension(upload) if upload and not too_large(upload) else ""
        if upload and not extension:
            messages.error(request, _("Das Logo muss ein Bild sein (JPEG, PNG, GIF oder WEBP)."))
            return redirect("school_settings")
        if upload:
            school.logo.save(f"logo.{extension}", upload, save=False)
        school.save()
        audit.log("settings", "Schuleinstellungen geändert", request=request)
        messages.success(request, _("Gespeichert."))
        return redirect("school_settings")
    return render(request, "core/school.html", {"school": school})


def _may_open_upload(user, name):
    """Files of a module's section have their own views. Without person.view, an account gets the logo and what a module allows."""
    if name.startswith(tuple(prefix for prefixes in modules.hooks("private_media") for prefix in prefixes)):
        return False
    if user.can("person.view") or name == SchoolSettings.load().logo.name:
        return True
    return any(allowed(user, name) for allowed in modules.hooks("media_access"))


def _send_upload(name, cache):
    """Only plain pictures open in the browser. Everything else is saved, so nothing in it runs on this address.

    Read through the storage, which decrypts the file when MEDIA_KEY is set."""
    filename = PurePosixPath(name).name
    inline = mimetypes.guess_type(filename)[0] in INLINE_TYPES
    response = FileResponse(default_storage.open(name), as_attachment=not inline, filename=filename)
    response["Cache-Control"] = cache
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "sandbox; default-src 'none'"
    return response


@require_GET
def media_file(request, path):
    """Uploaded files need a signed-in user, so pupil photos are never public."""
    root = Path(settings.MEDIA_ROOT).resolve()
    target = (root / path).resolve()
    if root not in target.parents or not target.is_file():
        raise Http404
    name = target.relative_to(root).as_posix()
    if not _may_open_upload(request.user, name):
        raise PermissionDenied
    return _send_upload(name, "private, max-age=3600")


@require_GET
def person_thumb(request, pk):
    from people.models import Person
    from people.thumbs import thumbnail

    name = Person.objects.filter(pk=pk).values_list("photo", flat=True).first()
    if not name or not (Path(settings.MEDIA_ROOT) / name).is_file():
        raise Http404
    if not _may_open_upload(request.user, name):
        raise PermissionDenied
    return _send_upload(thumbnail(name), "private, max-age=604800, immutable")


@require_GET
def school_logo(request):
    """The only upload that needs no sign-in, because the login page shows it."""
    logo = SchoolSettings.load().logo
    if not logo or not Path(logo.path).is_file():
        raise Http404
    return _send_upload(logo.name, "public, max-age=3600")


@require_GET
def healthz(request):
    """For the container health check. Says nothing about the installation."""
    try:
        # From the catalog, because a running restore holds locks on the table itself.
        with connection.cursor() as cursor:
            cursor.execute("select to_regclass('core_schoolsettings')" if connection.vendor == "postgresql" else "select 1")
            if cursor.fetchone()[0] is None:
                raise DatabaseError("not migrated")
        Path(settings.MEDIA_ROOT).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=settings.MEDIA_ROOT):
            pass
    except (DatabaseError, OSError):
        return HttpResponse("not ok", content_type="text/plain", status=503)
    return HttpResponse("ok", content_type="text/plain")


def csrf_failure(request, reason=""):
    return render(request, "csrf.html", status=403)


@require("admin.update")
def updates_page(request):
    updates.refresh_deployment()
    return render(request, "core/updates.html", {"status": updates.public_status(), "active_nav": "settings_home"})


@require("admin.update")
@require_GET
def update_status(request):
    updates.refresh_deployment()
    return JsonResponse(updates.public_status())


@require("admin.update")
@require_POST
def update_request(request):
    action = request.POST.get("action")
    if action == "check":
        updates.check_for_update()
        return JsonResponse(updates.public_status())
    if action == "restore":
        try:
            updates.restore_on_request()
        except updates.UpdateError as exc:
            return JsonResponse({"error": str(exc)}, status=409)
        audit.log("update", "Datenbank auf Wunsch auf den Stand vor der Aktualisierung zurückgesetzt", request=request)
        return JsonResponse(updates.public_status())
    if action != "apply" or not updates.enabled():
        return HttpResponseBadRequest()
    if updates.is_running():
        return JsonResponse({"error": _("Eine Aktualisierung läuft bereits.")}, status=409)
    try:
        updates.start_update()
    except updates.UpdateError as exc:
        audit.log("update", f"Aktualisierung nicht gestartet: {exc}", request=request)
        return JsonResponse({"error": str(exc)}, status=502)
    audit.log("update", "Aktualisierung gestartet", request=request)
    return JsonResponse(updates.public_status())


@require_GET
def manifest(request):
    school = SchoolSettings.load()
    return JsonResponse(
        {
            "name": school.name, "short_name": school.short_name, "start_url": "/", "display": "standalone",
            "background_color": "#f6f7f9", "theme_color": "#2553eb", "lang": "de",
            "icons": [
                {"src": static("img/app-192.png"), "sizes": "192x192", "type": "image/png"},
                {"src": static("img/app-512.png"), "sizes": "512x512", "type": "image/png"},
            ],
        }
    )


@require_GET
def service_worker(request):
    """Only makes the app installable. Nothing is cached, and the browser keeps the static files by itself."""
    body = """
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(
  caches.keys().then(keys => Promise.all(keys.map(k => caches.delete(k)))).then(() => clients.claim())
));
"""
    return HttpResponse(body, content_type="text/javascript")


def record_history(entity, record_id, limit=30):
    return ChangeLog.objects.filter(entity=entity, record_id=record_id).select_related("user")[:limit]
