from importlib.util import find_spec

from django.conf import settings
from django.urls import include, path

from . import modules

# Every app of the loaded modules that has URLs, in the order of schule/modules.py.
urlpatterns = [path("", include(f"{app}.urls")) for app in modules.apps() if find_spec(f"{app}.urls")]
if settings.OIDC_ENABLED:
    urlpatterns.append(path("oidc/", include("mozilla_django_oidc.urls")))
