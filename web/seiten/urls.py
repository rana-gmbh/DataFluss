from django.urls import path

from . import views

urlpatterns = [
    path("websites/", views.websites, name="websites"),
    path("websites/beantragen/", views.ask, name="sites_ask"),
    path("websites/aendern/", views.site_action, name="sites_action"),
    path("websites/melden/", views.report, name="sites_report"),
    path("pruefen/websites/", views.queue, name="sites_queue"),
    path("pruefen/websites/antrag/<int:pk>/", views.decide, name="sites_decide"),
    path("pruefen/websites/seite/<int:pk>/", views.switch, name="sites_switch"),
]
