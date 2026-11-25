"""Websites on seiten.datafluss.de (docs/website.md "Websites"): asking for them, making them, reporting them,
and the reviewers' part. Files are served by seiten/hosting.py on the sites' own host."""
import re

from django.conf import settings
from django.contrib import messages
from django.core.mail import send_mail
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone, translation
from django.utils.text import slugify
from django.utils.translation import gettext as _, gettext_noop
from django.views.decorators.http import require_POST

from konten import limits
from konten.decorators import named_required, reviewer_required
from publish import hosting as protocol

from . import hosting
from .models import Access, Site, SiteReport

RESERVED = {"api", "static", "media", "robots-txt", "admin", "www", "seiten", "vorlagen", "datafluss"}


def _notify_reviewers(subject, text):
    if settings.REVIEW_NOTIFY:
        send_mail(subject, text, None, settings.REVIEW_NOTIFY)


def _slug(name):
    base = slugify(name)[:36].strip("-") or "website"
    if base in RESERVED:
        base = f"{base}-seite"
    slug, n = base, 1
    while Site.objects.filter(slug=slug).exists():
        n += 1
        slug = f"{base}-{n}"
    return slug


def _rows(user):
    return [{"site": s, "url": hosting.url(s), "used": protocol.usage(hosting.root(s))} for s in user.sites.all()]


def websites(request):
    """What websites are, and for a signed-in account its access and its sites."""
    access = getattr(request.user, "site_access", None) if request.user.is_authenticated else None
    return render(request, "seiten/websites.html", {
        "access": access, "rows": _rows(request.user) if access and access.granted else [],
        "limit": settings.SITES_PER_ACCOUNT, "quota_mb": settings.SITES_QUOTA_MB, "sites_url": settings.SITES_URL,
        "stamp": limits.form_stamp(),
    })


@require_POST
@named_required
def ask(request):
    reason = " ".join(request.POST.get("reason", "").split())[:1000]
    if hasattr(request.user, "site_access"):
        return redirect("websites")
    if len(reason) < 10:
        messages.error(request, _("Bitte kurz beschreiben, was Sie veröffentlichen möchten."))
        return redirect("websites")
    Access.objects.create(user=request.user, reason=reason)
    _notify_reviewers(_("Neuer Antrag auf Websites"), f"{request.user.email}: {reason}")
    messages.success(request, _("Danke! Ihr Antrag wird geprüft; Sie bekommen eine E-Mail."))
    return redirect("websites")


def _granted(request):
    access = getattr(request.user, "site_access", None)
    return access is not None and access.granted


@require_POST
@named_required
def site_action(request):
    """Make a site, give it a new token, or delete it. A token is shown this once."""
    if not _granted(request):
        messages.error(request, _("Websites sind für Ihr Konto noch nicht freigeschaltet."))
        return redirect("websites")
    action, token, site = request.POST.get("action"), "", None
    if action == "create":
        name = " ".join(request.POST.get("name", "").split())[:100]
        if not name:
            messages.error(request, _("Bitte einen Namen eingeben."))
        elif request.user.sites.count() >= settings.SITES_PER_ACCOUNT:
            messages.error(request, _("Mehr als %(n)s Websites gehen nicht.") % {"n": settings.SITES_PER_ACCOUNT})
        else:
            token = protocol.new_token()
            site = Site.objects.create(owner=request.user, name=name, slug=_slug(name), token_hash=protocol.token_hash(token))
    else:
        site = get_object_or_404(Site, pk=request.POST.get("site"), owner=request.user)
        if action == "token":
            token = protocol.new_token()
            site.token_hash = protocol.token_hash(token)
            site.save()
        elif action == "delete":
            protocol.remove_all(hosting.root(site))
            site.delete()
            messages.success(request, _("Die Website ist gelöscht."))
    if not token:
        return redirect("websites")
    return render(request, "seiten/token.html", {"site": site, "token": token, "url": hosting.url(site), "sites_url": settings.SITES_URL})


@require_POST
def report(request):
    """Anyone may report a site, with its address and a reason."""
    if not limits.looks_human(request):
        return redirect("websites")
    address = request.POST.get("address", "")
    match = re.search(r"/([a-z0-9-]{1,40})/?", address.replace(settings.SITES_URL, "/"))
    site = Site.objects.filter(slug=match.group(1)).first() if match else None
    reason = " ".join(request.POST.get("reason", "").split())[:1000]
    if site is None or not reason:
        messages.error(request, _("Bitte die Adresse der Website und einen Grund angeben."))
    elif limits.allowed("report", limits.client_ip(request)):
        SiteReport.objects.create(site=site, reason=reason, reporter=request.user if request.user.is_authenticated else None)
        _notify_reviewers(_("Neue Meldung einer Website"), f"{hosting.url(site)}: {reason}")
        messages.success(request, _("Danke, wir sehen es uns an."))
    else:
        messages.error(request, _("Heute wurde schon viel gemeldet. Bitte morgen weiter."))
    return redirect("websites")


# ---------------------------------------------------------------- reviewers

@reviewer_required
def queue(request):
    return render(request, "seiten/queue.html", {
        "requests": Access.objects.filter(status=Access.REQUESTED).select_related("user").order_by("requested"),
        "reports": SiteReport.objects.filter(handled=False).select_related("site", "site__owner", "reporter"),
        "sites": [{"site": s, "url": hosting.url(s), "used": protocol.usage(hosting.root(s))}
                  for s in Site.objects.select_related("owner").order_by("-created")[:100]],
        "blocked": Access.objects.filter(status__in=(Access.REFUSED, Access.BLOCKED)).select_related("user").order_by("-decided")[:30],
    })


def _tell(access, subject, template):
    with translation.override(access.user.language or "de"):
        text = render_to_string(template, {"access": access, "url": settings.SITE_URL + "/websites/"})
        send_mail(_(subject), text, None, [access.user.email])


@require_POST
@reviewer_required
def decide(request, pk):
    access = get_object_or_404(Access, pk=pk)
    action, note = request.POST.get("action"), " ".join(request.POST.get("note", "").split())[:1000]
    if action in ("grant", "refuse", "block"):
        if action != "grant" and not note:
            messages.error(request, _("Bitte einen Grund für das Konto schreiben."))
            return redirect("sites_queue")
        access.status = {"grant": Access.GRANTED, "refuse": Access.REFUSED, "block": Access.BLOCKED}[action]
        access.note, access.decided, access.reviewer = note, timezone.now(), request.user
        access.save()
        if action == "grant":
            _tell(access, gettext_noop("Ihre Websites sind freigeschaltet"), "mail/sites_granted.txt")
        else:
            _tell(access, gettext_noop("Ihr Antrag auf Websites"), "mail/sites_refused.txt")
    return redirect("sites_queue")


@require_POST
@reviewer_required
def switch(request, pk):
    site = get_object_or_404(Site, pk=pk)
    action = request.POST.get("action")
    if action == "off":
        site.disabled, site.disabled_reason = True, " ".join(request.POST.get("note", "").split())[:300]
        site.save()
        SiteReport.objects.filter(site=site).update(handled=True)
    elif action == "on":
        site.disabled, site.disabled_reason = False, ""
        site.save()
    elif action == "done":
        SiteReport.objects.filter(site=site).update(handled=True)
    return redirect("sites_queue")
