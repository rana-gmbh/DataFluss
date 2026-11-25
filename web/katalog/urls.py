from django.urls import path

from . import api, moderation, views

urlpatterns = [
    path("", views.home, name="home"),
    path("vorlagen/<slug:key>/", views.detail, name="template_detail"),
    path("vorlagen/<slug:key>/herunterladen/", views.download, name="template_download"),
    path("vorlagen/<slug:key>/neue-version/", views.upload, name="template_new_version"),
    path("vorlagen/<slug:key>/zurueckziehen/", views.withdraw, name="template_withdraw"),
    path("vorlagen/<slug:key>/bewerten/", views.review, name="template_review"),
    path("vorlagen/<slug:key>/melden/", views.report, name="template_report"),
    path("bilder/<uuid:uid>.webp", views.picture, name="picture"),
    path("hochladen/", views.upload, name="upload"),
    path("meine-vorlagen/", views.my_templates, name="my_templates"),
    path("pruefen/", moderation.queue, name="review_queue"),
    path("pruefen/version/<int:pk>/", moderation.version_detail, name="review_version"),
    path("pruefen/bewertung/<int:pk>/", moderation.review_text, name="review_text"),
    path("pruefen/meldung/<int:pk>/", moderation.report_done, name="review_report"),
    path("impressum/", views.legal, {"page": "impressum"}, name="imprint"),
    path("datenschutz/", views.legal, {"page": "datenschutz"}, name="privacy"),
    path("nutzungsbedingungen/", views.legal, {"page": "bedingungen"}, name="terms"),
    path("api/v1/vorlagen.json", api.index, name="api_index"),
    path("api/v1/vorlagen/<slug:key>/<int:number>.json", api.file, name="api_file"),
    path("healthz/", views.healthz, name="healthz"),
]
