from django.urls import path

from . import views

urlpatterns = [
    path("import-export/", views.home, name="transfer"),
    path("import-export/hochladen/", views.upload, name="transfer_upload"),
    path("import-export/<uuid:pk>/zuordnung/", views.mapping, name="transfer_mapping"),
    path("import-export/<uuid:pk>/pruefung/", views.preview, name="transfer_preview"),
    path("import-export/<uuid:pk>/uebernehmen/", views.run_commit, name="transfer_commit"),
    path("import-export/<uuid:pk>/rueckgaengig/", views.undo, name="transfer_undo"),
    path("import-export/<uuid:pk>/verwerfen/", views.discard, name="transfer_discard"),
    path("export/zeilen/", views.export_rows, name="export_rows"),
    path("export/geraete/", views.export_assets, name="export_assets"),
    path("export/zugaenge/<slug:key>/", views.export_credentials, name="export_credentials"),
]
