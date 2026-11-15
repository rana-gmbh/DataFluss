from django.urls import path

from . import views

urlpatterns = [
    path("zugaenge/", views.overview, name="credentials"),
    path("zugaenge/<slug:key>/", views.platform_list, name="platform_list"),
    path("zugaenge/karte/<str:kind>/<uuid:owner>/<uuid:platform>/", views.login_card, name="login_card"),
    path("zugaenge/anzeigen/<uuid:pk>/<slug:field>/", views.reveal, name="credential_reveal"),
    path("einstellungen/plattformen/", views.platforms, name="platforms"),
    path("einstellungen/plattformen/<uuid:pk>/", views.platform_edit, name="platform_edit"),
]
