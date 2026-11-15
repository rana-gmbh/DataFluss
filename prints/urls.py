from django.urls import path

from . import views

urlpatterns = [
    path("berichte/", views.reports, name="reports"),
    path("berichte/drucken/", views.report_run, name="report_run"),
    path("berichte/neu/", views.template_new, name="template_new"),
    path("berichte/<uuid:pk>/gestalten/", views.template_designer, name="template_designer"),
    path("berichte/<uuid:pk>/vorschau/", views.template_preview, name="template_preview"),
    path("berichte/<uuid:pk>/speichern/", views.template_save, name="template_save"),
    path("berichte/<uuid:pk>/aktion/", views.template_action, name="template_action"),
    path("berichte/<uuid:pk>/version/<int:number>/", views.template_restore, name="template_restore"),
]
