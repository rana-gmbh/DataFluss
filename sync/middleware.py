"""On a Mac connected to a server, accounts, the school's settings, backups and updates belong to the
server: a pull would overwrite changes made here. Those pages explain that instead."""
import os

from django.shortcuts import render
from django.urls import resolve

# The update page stays open: on a computer it is about the app (core/views.py updates_page).
# The backup page stays open: it fetches the server's full backup (archive/transfer.py) and lists the
# files here, but a copy neither makes nor restores backups of its own.
SERVER_ONLY = {"password_change", "totp_setup", "users", "user_update", "two_factor_switch", "school_settings", "school_languages", "tables_tmdb",
               "update_status", "update_request", "backup_create", "backup_upload", "backup_check", "backup_restore",
               "backup_plan", "backup_plan_run", "backup_plan_off", "backup_transfer_check", "backup_transfer_run",
               # templates from datafluss.de or a file are installed on the server; the tables come by sync
               "table_template_preview", "table_catalog_switch", "table_catalog_update"}


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
