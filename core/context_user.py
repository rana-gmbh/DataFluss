from contextvars import ContextVar

_user = ContextVar("current_user", default=None)
_track = ContextVar("track_changes", default=True)


def set_current_user(user):
    return _user.set(user)


def get_current_user():
    user = _user.get()
    return user if user is not None and getattr(user, "is_authenticated", False) else None


class untracked:
    """Skip the change log for bulk work such as demo data."""

    def __enter__(self):
        self.token = _track.set(False)

    def __exit__(self, *exc):
        _track.reset(self.token)


def tracking_enabled():
    return _track.get()
