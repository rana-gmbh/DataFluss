from decimal import Decimal

from django import template
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
    shown = f"{value:.1f}".replace(".0", "").replace(".", ",")
    label = _("%(n)s von 5 Sternen") % {"n": shown}
    # The policy allows no inline styles, so the filled share is a class per half star (p0 … p100).
    half = int((value * 2).to_integral_value(rounding="ROUND_HALF_UP"))
    return format_html('<span class="stars {}" role="img" aria-label="{}"><span class="stars-on p{}"></span></span>', size, label, half * 10)


@register.filter
def comma(value):
    return f"{Decimal(str(value or 0)):.1f}".replace(".", ",")
