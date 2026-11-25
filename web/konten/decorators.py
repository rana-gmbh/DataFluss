from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse

TOTP_OK = "totp_ok"


def named_required(view):
    """Signed in and with a name, which is shown with what the account shares."""
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.name:
            return redirect(f"{reverse('account')}?next={request.get_full_path()}")
        return view(request, *args, **kwargs)
    return wrapped


def reviewer_required(view):
    """A reviewer who confirmed a code from the authenticator app in this session (set up once on first use)."""
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied
        if not request.session.get(TOTP_OK):
            return redirect(f"{reverse('totp')}?next={request.get_full_path()}")
        return view(request, *args, **kwargs)
    return wrapped
