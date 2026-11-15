from django.urls import path

from . import views, views_admin, views_client

urlpatterns = [
    path("sync/v1/info", views.info, name="sync_info"),
    path("sync/v1/register", views.register, name="sync_register"),
    path("sync/v1/module", views.module, name="sync_module"),
    path("sync/v1/pull", views.pull, name="sync_pull"),
    path("sync/v1/push", views.push, name="sync_push"),
    path("sync/v1/media", views.media, name="sync_media"),
    path("sync/v1/media-batch", views.media_batch, name="sync_media_batch"),
    path("sync/v1/whoami", views.whoami, name="sync_whoami"),
    path("einstellungen/synchronisation/", views_admin.overview, name="sync_overview"),
    path("einstellungen/synchronisation/mac/<uuid:pk>/trennen/", views_admin.revoke, name="sync_revoke"),
    path("einstellungen/synchronisation/abgleich/<uuid:pk>/", views_admin.resolve, name="sync_resolve"),
    path("sync/mac/status", views_client.status, name="sync_mac_status"),
    path("sync/mac/now", views_client.now, name="sync_mac_now"),
]
