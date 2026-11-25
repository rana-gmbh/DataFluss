"""Tags of the published site (templates/publish/site/): icons point into a sprite inside the page, so the
site works from a folder (file://) as well as on any web space."""
from django import template
from django.utils.html import format_html

register = template.Library()


@register.simple_tag
def sicon(name, classes=""):
    return format_html('<svg class="icon {}" aria-hidden="true"><use href="#i-{}"></use></svg>', classes, name)


@register.filter
def pct(value, top):
    from core.templatetags.ui import star_share

    return star_share(value, top or 5)


@register.filter
def web_url(value):
    """A link only to web pages: a value such as "javascript:…" stays plain text."""
    text = str(value or "").strip()
    return text if text.lower().startswith(("https://", "http://")) else ""
