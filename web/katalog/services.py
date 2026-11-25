"""What happens to templates, versions and reviews; the views only ask and show."""
import hashlib
import json
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Avg, Count, F, Max
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from tables import template_check as tc

from . import pictures as pics
from .models import CATEGORIES, LICENSE, Picture, Review, Template, Version

STARS = [Decimal(n) / 2 for n in range(1, 11)]  # 0.5 … 5


def reserved_keys(user=None):
    """Ids an upload by `user` may not use: the platform's own and those of other people's templates."""
    taken = Template.objects.exclude(owner=user) if user is not None else Template.objects.all()
    return tc.library_ids() | set(taken.values_list("key", flat=True))


def catalog_info(template):
    return {"source": "datafluss.de", "author": template.author, "license": LICENSE,
            "url": settings.SITE_URL + reverse("template_detail", args=[template.key])}


def file_bytes(version):
    return version.file.encode("utf-8")


def _notify_reviewers(subject, text):
    if settings.REVIEW_NOTIFY:
        send_mail(subject, text, None, settings.REVIEW_NOTIFY)


def submit(user, raw, category="sonstiges", changes="", uploads=(), template=None, official=False):
    """Checks an uploaded file and keeps it as a submitted version. Returns (version, problems)."""
    data, problems = tc.load(raw)
    if problems:
        return None, problems
    if not isinstance(data, dict):
        return None, [_("Die Datei ist keine gültige Vorlage.")]
    key = data.get("id")
    if template is None:
        existing = Template.objects.filter(key=key).first() if isinstance(key, str) else None
        if existing is not None and (existing.owner_id != getattr(user, "pk", None) or existing.official != official):
            return None, [_("Die Kennung %(id)s ist vergeben. Bitte eine eigene wählen.") % {"id": key}]
        template = existing
    elif key != template.key:
        return None, [_("Eine neue Version braucht dieselbe Kennung wie die Vorlage (%(id)s).") % {"id": template.key}]
    problems = tc.check_untrusted(data, reserved=set() if official else reserved_keys(user))
    if problems:
        return None, problems
    if template is not None:
        highest = template.versions.filter(status__in=[Version.APPROVED, Version.REPLACED]).aggregate(n=Max("number"))["n"] or 0
        if data["version"] <= highest:
            return None, [_("Die Versionsnummer muss größer sein als %(n)s, die zuletzt freigegebene.") % {"n": highest}]
    if category not in dict(CATEGORIES):
        category = "sonstiges"
    converted = []
    for upload in list(uploads)[:pics.MAX_FILES]:
        try:
            converted.append(pics.convert(upload))
        except pics.PictureError as exc:
            return None, [str(exc)]
    with transaction.atomic():
        if template is None:
            template = Template.objects.create(key=key, owner=None if official else user, category=category, official=official)
        else:
            template.category = category
            template.save(update_fields=["category"])
        # A version submitted again replaces the one still waiting, also one with the same number.
        template.versions.filter(status=Version.SUBMITTED).update(status=Version.WITHDRAWN)
        template.versions.filter(number=data["version"]).exclude(status__in=[Version.APPROVED, Version.REPLACED]).delete()
        version = Version.objects.create(
            template=template, number=data["version"], data=tc.canonical(data), title=str(data["title"])[:100],
            description=str(data.get("description") or "")[:1000], changes=" ".join(str(changes or "").split())[:500],
            notes=tc.notes(data))
        for order, (content, width, height) in enumerate(converted):
            picture = Picture(version=version, width=width, height=height, order=order)
            picture.image.save("bild.webp", content, save=True)
    if not official:
        _notify_reviewers(_("Neue Vorlage zum Prüfen: %(title)s") % {"title": version.title},
                          settings.SITE_URL + reverse("review_version", args=[version.pk]))
    return version, []


def approve(version, reviewer, note=""):
    template = version.template
    with transaction.atomic():
        version.file = json.dumps(tc.canonical(version.data, catalog_info(template)), ensure_ascii=False, indent=1)
        version.sha256 = hashlib.sha256(file_bytes(version)).hexdigest()
        version.status, version.reviewer, version.reviewed, version.decision = Version.APPROVED, reviewer, timezone.now(), note
        version.save()
        template.versions.filter(status=Version.APPROVED).exclude(pk=version.pk).update(status=Version.REPLACED)
        summary = tc.summary(version.data)
        template.published, template.updated = version, timezone.now()
        template.search = " ".join([version.title, version.description, template.key, *[t["name"] for t in summary["tables"]]]).lower()
        template.save()
    _tell_owner(version, "mail/approved.txt", _("Ihre Vorlage ist freigegeben: %(title)s"))


def reject(version, reviewer, note):
    version.status, version.reviewer, version.reviewed, version.decision = Version.REJECTED, reviewer, timezone.now(), note
    version.save()
    _tell_owner(version, "mail/rejected.txt", _("Ihre Vorlage wurde nicht freigegeben: %(title)s"))


def _tell_owner(version, template_name, subject):
    owner = version.template.owner
    if owner is None:
        return
    text = render_to_string(template_name, {"version": version, "url": settings.SITE_URL + reverse("my_templates")})
    send_mail(subject % {"title": version.title}, text, None, [owner.email])


def parse_stars(raw):
    try:
        value = Decimal(str(raw).replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    return value if value in STARS else None


def rate(template, user, stars, text=""):
    """Sets the person's stars (counted at once) and text (shown once approved). Returns the review."""
    text = str(text or "").strip()[:2000]
    review = Review.objects.filter(template=template, user=user).first()
    now = timezone.now()
    if review is None:
        review = Review(template=template, user=user, created=now)
    # A new or changed text waits for a reviewer; the same text keeps its decision.
    if text and (review.pk is None or text != review.text):
        review.text_status = Review.SUBMITTED
    elif not text:
        review.text_status = Review.NONE
    review.stars, review.text, review.updated = stars, text, now
    review.version = template.published.number if template.published else 0
    review.save()
    recount(template)
    if review.text_status == Review.SUBMITTED:
        _notify_reviewers(_("Neue Bewertung zum Prüfen"), settings.SITE_URL + reverse("review_queue"))
    return review


def recount(template):
    figures = Review.objects.filter(template=template).aggregate(avg=Avg("stars"), n=Count("pk"))
    template.rating = Decimal(figures["avg"] or 0).quantize(Decimal("0.01"))
    template.ratings = figures["n"]
    template.save(update_fields=["rating", "ratings"])


def count_download(template):
    Template.objects.filter(pk=template.pk).update(downloads=F("downloads") + 1)


def forget_user(user):
    """Deletes an account: its reviews go, templates never published go, published ones stay under
    "gelöschtes Konto" (they were shared under CC BY 4.0) without anything still waiting."""
    with transaction.atomic():
        for template in Template.objects.filter(owner=user):
            if not template.versions.filter(status__in=[Version.APPROVED, Version.REPLACED]).exists():
                template.delete()
            else:
                template.versions.filter(status=Version.SUBMITTED).update(status=Version.WITHDRAWN)
        touched = list(Template.objects.filter(reviews__user=user).distinct())
        user.delete()
        for template in touched:
            recount(template)
