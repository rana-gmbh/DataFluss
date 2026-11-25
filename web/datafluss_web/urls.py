from django.urls import include, path

urlpatterns = [
    path("", include("konten.urls")),
    path("", include("katalog.urls")),
    path("", include("seiten.urls")),
]
