from decimal import Decimal

from django import template
from django.utils import translation
from django.utils.html import format_html
from django.utils.translation import gettext as _

register = template.Library()


@register.simple_tag
def stars(value, size=""):
    """Five stars filled up to `value` (half steps), drawn by CSS (static/web/site.css .stars)."""
    try:
        value = max(Decimal(0), min(Decimal(str(value or 0)), Decimal(5)))
    except Exception:
        value = Decimal(0)
    shown = _number(value).replace(",0", "").replace(".0", "")
    label = _("%(n)s von 5 Sternen") % {"n": shown}
    # The policy allows no inline styles, so the filled share is a class per half star (p0 … p100).
    half = int((value * 2).to_integral_value(rounding="ROUND_HALF_UP"))
    return format_html('<span class="stars {}" role="img" aria-label="{}"><span class="stars-on p{}"></span></span>', size, label, half * 10)


def _number(value):
    text = f"{Decimal(str(value or 0)):.1f}"
    return text.replace(".", ",") if (translation.get_language() or "de").startswith("de") else text


@register.filter
def comma(value):
    """A rating with one decimal, written as the page's language writes it (3,5 or 3.5)."""
    return _number(value)


@register.filter
def localized(version, name):
    """A version's title or description in the page's language, when the template brings one (titles/descriptions)."""
    data = getattr(version, "data", None) or {}
    own = (data.get(name + "s") or {}).get(translation.get_language() or "de") if isinstance(data.get(name + "s"), dict) else None
    return own or getattr(version, name, "")
