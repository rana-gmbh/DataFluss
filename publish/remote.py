"""On a computer connected to a server: that server's publications (publish/api.py), shown and published
again from here. The computer's own changes go to the server first, so the website gets them."""
import os

from django.utils.translation import gettext as _


def available():
    if not os.environ.get("SYNC_TOKEN"):
        return False
    from sync.models import ClientState

    state = ClientState.load()
    return state is not None and not state.revoked


def _call(path, body=None):
    from sync import client
    from sync.models import ClientState

    return client._api(ClientState.load(), os.environ["SYNC_TOKEN"], f"publish/{path}", body=body or {})


def publications():
    """(server address, allowed, [rows]); raises sync.client.SyncError when the server cannot be reached."""
    from sync.models import ClientState

    answer = _call("list")
    return ClientState.load().server_url, answer.get("allowed", False), answer.get("publications", [])


def publish(pk):
    """Send this computer's changes, then ask the server to publish. Returns (row or None, problem)."""
    from sync import client

    if client.WORKER is not None:
        result = client.WORKER.now()
        if result.get("state") == "error":
            return None, _("Der Abgleich mit dem Server hat nicht geklappt: %(error)s") % {"error": result.get("error", "")}
    try:
        return _call("run", {"id": str(pk)}).get("publication"), ""
    except client.SyncError as exc:
        return None, str(exc)
