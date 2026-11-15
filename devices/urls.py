from django.urls import path

from . import views

urlpatterns = [
    path("geraete/", views.assets, name="assets"),
    path("api/geraete/", views.api_assets, name="api_assets"),
    path("geraete/neu/", views.asset_new, name="asset_new"),
    path("geraete/vorschlag/", views.asset_suggest, name="asset_suggest"),
    path("geraete/typen/", views.categories, name="asset_categories"),
    path("geraete/orte/", views.places, name="places"),
    path("geraete/aktionen/", views.campaigns, name="campaigns"),
    path("geraete/aktionen/<uuid:pk>/", views.campaign_detail, name="campaign_detail"),
    path("geraete/<uuid:pk>/", views.asset_detail, name="asset_detail"),
    path("geraete/<uuid:pk>/panel/", views.asset_panel, name="asset_panel"),
    path("geraete/<uuid:pk>/rueckgabe/", views.asset_return, name="asset_return"),
    path("geraete/<uuid:pk>/aktion/", views.asset_action, name="asset_action"),
    path("geraete/<uuid:pk>/product-key/", views.asset_key, name="asset_key"),
    path("personen/<uuid:pk>/geraet/", views.person_issue, name="person_issue"),
    path("schaeden/", views.cases, name="cases"),
    path("schaeden/neu/", views.case_new, name="case_new"),
    path("schaeden/<uuid:pk>/", views.case_detail, name="case_detail"),
    path("schaeden/<uuid:pk>/panel/", views.case_panel, name="case_panel"),
]
