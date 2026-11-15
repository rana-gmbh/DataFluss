"""Requests as the desktop client's shell forwards them to the sidecar.

WebKit sends neither Origin nor Referer with an XMLHttpRequest on the app's custom scheme, so the
shell adds the sidecar's origin (client/shell/src-tauri/src/sidecar.rs, sidecar_headers). These
tests keep Django's CSRF check on, which the normal test client switches off.
"""
from django.test import Client, override_settings
from django.urls import reverse

from people.models import Person

from .base import AppTestCase

SIDECAR = "https://localhost"


@override_settings(ALLOWED_HOSTS=["localhost"], CSRF_TRUSTED_ORIGINS=[SIDECAR])
class SidecarCsrfTests(AppTestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True, HTTP_HOST="localhost")
        self.login(self.sek)
        page = self.client.get(reverse("person_detail", args=[self.pupil.pk]), secure=True)
        self.assertEqual(page.status_code, 200)
        self.token = page.context["csrf_token"]

    def save(self, **headers):
        return self.client.post(
            reverse("person_field", args=[self.pupil.pk, "first_name"]), {"value": "Lea"},
            secure=True, HTTP_HX_REQUEST="true", HTTP_X_CSRFTOKEN=str(self.token), **headers,
        )

    def test_htmx_save_with_the_origin_the_shell_adds(self):
        self.assertEqual(self.save(HTTP_ORIGIN=SIDECAR).status_code, 204)
        self.assertEqual(Person.objects.get(pk=self.pupil.pk).first_name, "Lea")

    def test_without_origin_or_referer_django_refuses(self):
        """What happened before the shell added the origin: every inline edit failed."""
        self.assertEqual(self.save().status_code, 403)

    def test_a_foreign_origin_is_refused(self):
        self.assertEqual(self.save(HTTP_ORIGIN="app://other").status_code, 403)
