"""Values of a mask tree that reach markup as something other than escaped text.

A tab's id goes into Alpine expressions (masks/_form.html) and an item's width into a style; HTML escaping does
not protect either, and a tree may come from a template someone else wrote (tables/blueprints.py). So both are
reduced to what they may be before rendering.
"""
import re

TAB_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def tab_id(raw, index):
    """The tab's own id when it is a plain token, otherwise one made from its position."""
    raw = str(raw or "")
    return raw if TAB_ID.match(raw) else f"tab{index + 1}"


def width(raw, default=12):
    """A width in grid columns, 1 to 12."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return min(max(value, 1), 12)
