"""Which kinds of people a user may see, edit or archive (roles: areas Schüler, Personal, Eltern).

"person.view" and friends hold when one kind is allowed (core/permissions.py DERIVED); pages that show one
person or one kind ask here instead.
"""
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

KINDS = ("pupil", "staff")


def may(user, kind, action="view"):
    return user.can(f"{kind}.{action}")


def kinds(user, action="view"):
    """The kinds of Person (pupil, staff) the user may see, edit or archive."""
    return [kind for kind in KINDS if may(user, kind, action)]


def person_or_403(request, pk, action="view", queryset=None):
    from .models import Person

    person = get_object_or_404(queryset if queryset is not None else Person.objects.all(), pk=pk)
    if not may(request.user, person.kind, action):
        raise PermissionDenied
    return person
