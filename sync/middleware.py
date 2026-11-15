"""On a Mac connected to a server, accounts, the school's settings, backups and updates belong to the
server: a pull would overwrite changes made here. Those pages explain that instead."""
import os

from django.shortcuts import render
from django.urls import resolve

# The backup page stays open: it fetches the server's full backup (archive/transfer.py) and lists the
# files here, but a copy neither makes nor restores backups of its own.
SERVER_ONLY = {"password_change", "totp_setup", "users", "user_update", "two_factor_switch", "school_settings", "school_languages",
               "updates", "update_status", "update_request", "backup_create", "backup_upload", "backup_check", "backup_restore",
               "backup_plan", "backup_plan_run", "backup_plan_off", "backup_transfer_check", "backup_transfer_run"}


class ConnectedMacMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if os.environ.get("SYNC_TOKEN"):
            try:
                name = resolve(request.path).url_name
            except Exception:
                name = ""
            if name in SERVER_ONLY:
                return render(request, "sync/server_only.html", status=403)
        return self.get_response(request)
