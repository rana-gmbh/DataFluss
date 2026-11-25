from django.urls import path

from . import views

urlpatterns = [
    path("anmelden/", views.login_view, name="login"),
    path("anmelden/link/<str:token>/", views.login_link, name="login_link"),
    path("abmelden/", views.logout_view, name="logout"),
    path("sprache/<str:code>/", views.language, name="language"),
    path("konto/", views.account, name="account"),
    path("konto/zweiter-faktor/", views.totp, name="totp"),
]
