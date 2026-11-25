"""Signing in by link, the account page and the reviewers' second factor."""
import pyotp
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from . import limits, tokens
from .decorators import TOTP_OK
from .models import User


def _next(request, fallback="home"):
    target = request.POST.get("next") or request.GET.get("next") or ""
    if url_has_allowed_host_and_scheme(target, {request.get_host()}, require_https=request.is_secure()):
        return target
    return reverse(fallback)


def login_view(request):
    if request.user.is_authenticated:
        return redirect(_next(request))
    error = ""
    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()[:254]
        try:
            validate_email(email)
        except ValidationError:
            error = _("Bitte eine gültige E-Mail-Adresse eingeben.")
        if not error and not limits.looks_human(request):
            error = _("Das ging zu schnell. Bitte noch einmal versuchen.")
        if not error and not (limits.allowed("mail_ip", limits.client_ip(request)) and limits.allowed("mail_address", email)):
            error = _("Zu viele Anmeldeversuche. Bitte später noch einmal versuchen.")
        if not error:
            user = User.objects.filter(email=email).first()
            link = settings.SITE_URL + reverse("login_link", args=[tokens.make(email, user)])
            if request.POST.get("next"):
                link += "?next=" + _next(request)
            text = render_to_string("mail/login.txt", {"link": link, "new": user is None})
            send_mail(_("Anmelden bei DataFluss Vorlagen"), text, None, [email])
            # The same answer whether or not the address has an account, so nobody can find out who has one.
            return render(request, "konten/sent.html", {"email": email})
    return render(request, "konten/login.html", {"error": error, "stamp": limits.form_stamp(), "next": request.GET.get("next", "")})


def login_link(request, token):
    """GET shows a button, POST signs in: mail programs that open links to check them do not spend the link."""
    email = tokens.read(token)
    if email is None:
        return render(request, "konten/link_invalid.html", status=400)
    if request.method == "POST":
        user, created = User.objects.get_or_create(email=email, defaults={"name": ""})
        if created:
            user.set_unusable_password()
            user.save()
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        if user.language != request.LANGUAGE_CODE:  # mails about their templates follow the pages they use
            user.language = request.LANGUAGE_CODE
            user.save(update_fields=["language"])
        if not user.name:
            return redirect(f"{reverse('account')}?next={_next(request)}")
        return redirect(_next(request))
    return render(request, "konten/link.html", {"email": email, "next": request.GET.get("next", "")})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("home")


@login_required
def account(request):
    if request.method == "POST" and request.POST.get("action") == "name":
        name = " ".join(request.POST.get("name", "").split())[:60]
        if not name:
            messages.error(request, _("Bitte einen Namen angeben."))
        else:
            request.user.name = name
            request.user.save(update_fields=["name"])
            messages.success(request, _("Gespeichert."))
            if request.POST.get("next"):
                return redirect(_next(request))
        return redirect("account")
    if request.method == "POST" and request.POST.get("action") == "delete":
        if request.POST.get("confirm", "").strip().lower() != request.user.email:
            messages.error(request, _("Zum Löschen bitte die eigene E-Mail-Adresse eingeben."))
            return redirect("account")
        from katalog.services import forget_user

        user = request.user
        logout(request)
        forget_user(user)
        messages.success(request, _("Das Konto ist gelöscht."))
        return redirect("home")
    from katalog.models import Review, Template

    return render(request, "konten/account.html", {
        "next": request.GET.get("next", ""), "templates": Template.objects.filter(owner=request.user).count(),
        "reviews": Review.objects.filter(user=request.user).count(),
    })


@login_required
def totp(request):
    """A reviewer's second factor: set up once (secret shown as QR code and text), then a code per session."""
    if not request.user.is_staff:
        return redirect("home")
    user, error = request.user, ""
    secret = user.totp_secret or request.session.setdefault("totp_new", pyotp.random_base32())
    if request.method == "POST":
        code = "".join(request.POST.get("code", "").split())
        if not limits.allowed("mail_address", f"totp:{user.pk}"):
            error = _("Zu viele Versuche. Bitte später noch einmal versuchen.")
        elif pyotp.TOTP(secret).verify(code, valid_window=1):
            if not user.totp_secret:
                user.totp_secret = secret
                user.save(update_fields=["totp_secret"])
                request.session.pop("totp_new", None)
            request.session[TOTP_OK] = True
            return redirect(_next(request, "review_queue"))
        else:
            error = _("Der Code stimmt nicht.")
    context = {"error": error, "setup": not user.totp_secret, "next": request.GET.get("next", "")}
    if not user.totp_secret:
        import segno

        uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name="DataFluss Vorlagen")
        qr = segno.make(uri, error="m").svg_data_uri(scale=5, border=2, dark="#111827", light="#ffffff")
        context.update(secret=secret, qr=qr)
    return render(request, "konten/totp.html", context)
