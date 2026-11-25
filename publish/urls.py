from django.urls import path

from . import views

urlpatterns = [
    path("einstellungen/veroeffentlichen/", views.publication_list, name="publication_list"),
    path("einstellungen/veroeffentlichen/<uuid:pk>/", views.publication_detail, name="publication_detail"),
    path("einstellungen/veroeffentlichen/<uuid:pk>/testen/", views.publication_test, name="publication_test"),
    path("einstellungen/veroeffentlichen/<uuid:pk>/start/", views.publication_confirm, name="publication_confirm"),
    path("einstellungen/veroeffentlichen/<uuid:pk>/stand/", views.publication_status, name="publication_status"),
    path("einstellungen/veroeffentlichen/<uuid:pk>/zurueckziehen/", views.publication_withdraw, name="publication_withdraw"),
    path("einstellungen/veroeffentlichen/<uuid:pk>/loeschen/", views.publication_delete, name="publication_delete"),
    path("einstellungen/veroeffentlichen/<uuid:pk>/zip/", views.publication_zip, name="publication_zip"),
    path("einstellungen/veroeffentlichen/<uuid:pk>/vorschau/", views.publication_preview, name="publication_preview"),
    path("einstellungen/veroeffentlichen/<uuid:pk>/vorschau/<path:path>", views.publication_preview, name="publication_preview_file"),
    # The sites this server hosts: public (core/middleware.py PUBLIC_NAMES).
    path("p/<slug:slug>/", views.site_file, name="site_file"),
    path("p/<slug:slug>/<path:path>", views.site_file, name="site_file_path"),
]
