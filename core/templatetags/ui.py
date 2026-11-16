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
