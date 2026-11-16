from django.urls import path

from . import views

urlpatterns = [
    path("einstellungen/tabellen/", views.table_list, name="table_list"),
    path("einstellungen/tabellen/<slug:key>/", views.table_settings, name="table_settings"),
    path("einstellungen/tabellen/<slug:key>/rechte/", views.table_rights, name="table_rights"),
    path("einstellungen/tabellen/<slug:key>/felder/neu/", views.table_field_new, name="table_field_new"),
    path("einstellungen/tabellen/<slug:key>/felder/reihenfolge/", views.table_field_order, name="table_field_order"),
    path("einstellungen/tabellen/<slug:key>/felder/<uuid:pk>/", views.table_field, name="table_field"),
]
