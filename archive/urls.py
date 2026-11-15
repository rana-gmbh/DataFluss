from django.urls import path

from . import transfer, views

urlpatterns = [
    path("einstellungen/sicherung/", views.home, name="backups"),
    path("einstellungen/sicherung/status/", views.status, name="backup_status"),
    path("einstellungen/sicherung/erstellen/", views.create, name="backup_create"),
    path("einstellungen/sicherung/hochladen/", views.upload, name="backup_upload"),
    path("einstellungen/sicherung/pruefen/", views.check, name="backup_check"),
    path("einstellungen/sicherung/einspielen/", views.restore, name="backup_restore"),
    path("einstellungen/sicherung/loeschen/", views.delete, name="backup_delete"),
    path("einstellungen/sicherung/herunterladen/<str:name>/", views.download, name="backup_download"),
    path("einstellungen/sicherung/plan/", views.plan_save, name="backup_plan"),
    path("einstellungen/sicherung/plan/jetzt/", views.plan_run, name="backup_plan_run"),
    path("einstellungen/sicherung/plan/aus/", views.plan_off, name="backup_plan_off"),
    path("einstellungen/sicherung/server/pruefen/", views.transfer_check, name="backup_transfer_check"),
    path("einstellungen/sicherung/server/uebertragen/", views.transfer_run, name="backup_transfer_run"),
    path("einstellungen/sicherung/vom-server/", views.server_backup, name="backup_from_server"),
    # The server's side, for a computer (no session: a ticket after signing in).
    path("sync/v1/transfer/start", transfer.api_transfer_start, name="sync_transfer_start"),
    path("sync/v1/transfer/chunk", transfer.api_transfer_chunk, name="sync_transfer_chunk"),
    path("sync/v1/transfer/finish", transfer.api_transfer_finish, name="sync_transfer_finish"),
    path("sync/v1/transfer/status", transfer.api_transfer_status, name="sync_transfer_status"),
    path("sync/v1/backup/start", transfer.api_backup_start, name="sync_backup_start"),
    path("sync/v1/backup/file", transfer.api_backup_file, name="sync_backup_file"),
]
