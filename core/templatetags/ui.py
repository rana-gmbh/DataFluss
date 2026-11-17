from django import template
from django.templatetags.static import static
from django.utils.html import format_html
from django.utils.translation import gettext as _

register = template.Library()


@register.simple_tag
def icon(name, classes=""):
    return format_html('<svg class="icon {}" aria-hidden="true"><use href="{}#{}"></use></svg>', classes, static("img/icons.svg"), name)


@register.filter
def can(user, capability):
    return user.can(capability)


@register.filter
def edits_kind(user, kind):
    """Whether the user may change people of this kind ("pupil", "staff"): roles grant it per kind."""
    return user.can(f"{kind}.edit")


@register.filter
def archives_kind(user, kind):
    return user.can(f"{kind}.archive")


@register.filter
def get(mapping, key):
    return mapping.get(key, "") if mapping else ""


@register.simple_tag
def avatar(person, size=32, zoom=False):
    url = person.photo.url if getattr(person, "photo", None) else ""
    small = person.thumb_url if url else ""
    style = f"width:{size}px;height:{size}px;font-size:{max(size // 2.6, 10)}px"
    if url and zoom:
        return format_html(
            '<button type="button" class="avatar photo-zoom-trigger" style="{}" data-zoom="{}" aria-label="{}"><img src="{}" alt=""></button>',
            style, url, _("Foto vergrößern"), small,
        )
    if url:
        return format_html('<span class="avatar" style="{}"><img src="{}" alt="" loading="lazy"></span>', style, small)
    return format_html('<span class="avatar" style="{}">{}</span>', style, person.initials)


STAR_PATH = "M12 1.8l3.09 6.26 6.91 1-5 4.87 1.18 6.88L12 17.56l-6.18 3.25L7 13.93 2 9.06l6.91-1z"


def star_share(value, top):
    """How much of the row of stars is filled, in percent: stars are 24 wide with a gap of 2, so a half star
    ends in the middle of its star and not halfway along the gaps (static/js/app.js App.starShare)."""
    from decimal import Decimal, InvalidOperation

    try:
        value = max(Decimal(0), min(Decimal(str(value or 0)), Decimal(top)))
    except InvalidOperation:
        return 0
    whole = int(value)
    covered = whole * 26 + (value - whole) * 24 if value - whole else max(whole * 26 - 2, 0)
    return round(float(covered) / (26 * top - 2) * 100, 2)


def star_row(top):
    paths = "".join(f'<path transform="translate({i * 26} 0)" d="{STAR_PATH}"/>' for i in range(top))
    return f'<svg viewBox="0 0 {26 * top - 2} 24" fill="currentColor" aria-hidden="true">{paths}</svg>'


@register.simple_tag
def stars(value, top=5, size=""):
    """A row of stars filled up to `value` (0.5 steps), for a rating field (core/fieldtypes.py "rating")."""
    from django.utils.safestring import mark_safe

    try:
        top = max(3, min(int(top or 5), 10))
    except (TypeError, ValueError):
        top = 5
    row = star_row(top)
    label = _("%(n)s von %(top)s Sternen") % {"n": str(value or 0).replace(".0", "").replace(".", ","), "top": top}
    return mark_safe(f'<span class="stars {size}" style="--pct:{star_share(value, top)}%" role="img" aria-label="{label}">'
                     f'<span class="stars-off">{row}</span><span class="stars-on">{row}</span></span>')
