"""datafluss.de itself: the product's main page, on the same site as the template catalog but with URLs of its own.
www.datafluss.de redirects to it. The main page needs no session and sets no cookie."""
from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponsePermanentRedirect


def host():
    return (urlsplit(settings.HOME_URL).hostname or "").lower()


class HomeHostMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        name = request.get_host().split(":")[0].lower()
        if name == "www." + host():
            return HttpResponsePermanentRedirect(settings.HOME_URL + request.get_full_path())
        if name == host():
            request.urlconf = "home.urls"
        return self.get_response(request)
