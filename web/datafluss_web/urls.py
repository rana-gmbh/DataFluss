from django.urls import include, path
from django.views.i18n import set_language

urlpatterns = [
    path("sprache/", set_language, name="set_language"),
    path("", include("konten.urls")),
    path("", include("katalog.urls")),
]
