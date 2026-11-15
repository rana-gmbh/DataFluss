from django.urls import path

from . import views

urlpatterns = [
    path("einstellungen/masken/", views.mask_list, name="mask_list"),
    path("einstellungen/masken/<uuid:pk>/", views.mask_builder, name="mask_builder"),
    path("einstellungen/masken/<uuid:pk>/speichern/", views.mask_save, name="mask_save"),
    path("einstellungen/masken/<uuid:pk>/vorschau/", views.mask_preview, name="mask_preview"),
    path("einstellungen/masken/<uuid:pk>/aktion/", views.mask_action, name="mask_action"),
    path("einstellungen/felder/", views.field_list, name="field_list"),
    path("einstellungen/felder/neu/", views.field_create, name="field_create"),
    path("einstellungen/felder/<uuid:pk>/", views.field_edit, name="field_edit"),
    path("einstellungen/felder/<uuid:pk>/duplizieren/", views.field_duplicate, name="field_duplicate"),
]
