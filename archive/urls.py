from django.urls import path

from . import views

urlpatterns = [
    path("einstellungen/sicherung/", views.home, name="backups"),
    path("einstellungen/sicherung/status/", views.status, name="backup_status"),
    path("einstellungen/sicherung/erstellen/", views.create, name="backup_create"),
    path("einstellungen/sicherung/hochladen/", views.upload, name="backup_upload"),
    path("einstellungen/sicherung/pruefen/", views.check, name="backup_check"),
    path("einstellungen/sicherung/einspielen/", views.restore, name="backup_restore"),
    path("einstellungen/sicherung/loeschen/", views.delete, name="backup_delete"),
    path("einstellungen/sicherung/herunterladen/<str:name>/", views.download, name="backup_download"),
]
