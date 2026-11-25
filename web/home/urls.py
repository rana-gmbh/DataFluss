"""The URLs of datafluss.de (home/middleware.py switches to them by host)."""
from django.urls import path
from katalog import views as katalog_views
from konten import views as konten_views

from . import views

# Legal pages live once, on the template site (one text for all three hosts); the main page links there.
urlpatterns = [
    path("", views.index, name="home"),
    path("sprache/<str:code>/", konten_views.language, name="language"),
    path("healthz/", katalog_views.healthz, name="healthz"),
    path("robots.txt", views.robots, name="robots"),
]

handler404 = "home.views.not_found"  # the catalog's 404 page needs the catalog's URLs
