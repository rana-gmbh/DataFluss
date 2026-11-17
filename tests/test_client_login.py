"""The remembered sign-in of the desktop client ("Anmeldedaten merken").

The sign-in page offers the box only in the client (SCHULE_CLIENT_DB_NAME is set by its shell) and tells the
shell how a sign-in went with "X-DataFluss-Login". The shell keeps username and password in the Keychain and
signs in by itself the next time (client/shell/src-tauri/src/login.rs, Sidecar::sign_in): it reads the CSRF
token from the page and posts the form with the sidecar's origin. These tests go the same way.
"""
import re
from unittest import mock

from django.test import Client, override_settings
from django.urls import reverse

from core.models import AuditEvent, User

from .base import AppTestCase

SIDECAR = "https://localhost"
PASSWORD = "x" * 14
CLIENT_ENV = {"SCHULE_CLIENT_DB_NAME": "Testschule"}
# What login.rs looks for in the page.
TOKEN = re.compile(r'name="csrfmiddlewaretoken" value="([A-Za-z0-9]+)"')


class ServerSignInTests(AppTestCase):
    def test_a_server_offers_no_box_and_sends_no_answer_header(self):
        page = self.client.get(reverse("login"))
        self.assertNotContains(page, 'name="remember" value="1"')
        answer = self.client.post(reverse("login"), {"username": "sek", "password": PASSWORD})
        self.assertEqual(answer.status_code, 302)
        self.assertNotIn("X-DataFluss-Login", answer.headers)

    def test_a_server_ignores_the_shells_header(self):
        page = self.client.get(reverse("login"), HTTP_X_DATAFLUSS_REMEMBERED="forgotten")
        self.assertNotContains(page, "wurde vergessen")


@mock.patch.dict("os.environ", CLIENT_ENV)
class ClientSignInPageTests(AppTestCase):
    def test_the_box_is_offered_and_unticked(self):
        page = self.client.get(reverse("login"))
        self.assertContains(page, 'name="remember" value="1"')
        self.assertFalse(page.context["remembered"])

    def test_ticked_when_the_shell_keeps_a_sign_in(self):
        page = self.client.get(reverse("login"), HTTP_X_DATAFLUSS_REMEMBERED="1")
        self.assertTrue(page.context["remembered"])
        self.assertContains(page, "checked")

    def test_says_when_a_remembered_sign_in_was_forgotten(self):
        page = self.client.get(reverse("login"), HTTP_X_DATAFLUSS_REMEMBERED="forgotten")
        self.assertTrue(page.context["remembered_forgotten"])
        self.assertFalse(page.context["remembered"])

    def test_success_is_ok(self):
        answer = self.client.post(reverse("login"), {"username": "sek", "password": PASSWORD, "remember": "1"})
        self.assertEqual(answer.status_code, 302)
        self.assertEqual(answer.headers["X-DataFluss-Login"], "ok")

    def test_a_wrong_password_is_failed_and_keeps_the_box(self):
        answer = self.client.post(reverse("login"), {"username": "sek", "password": "wrong", "remember": "1"})
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.headers["X-DataFluss-Login"], "failed")
        self.assertTrue(answer.context["remembered"])

    def test_too_many_failures_is_locked_not_failed(self):
        """A lock passes; the shell keeps the sign-in instead of forgetting a password that may still fit."""
        for _ in range(8):
            self.client.post(reverse("login"), {"username": "sek", "password": "wrong"})
        answer = self.client.post(reverse("login"), {"username": "sek", "password": PASSWORD})
        self.assertEqual(answer.headers["X-DataFluss-Login"], "locked")

    def test_the_password_lands_nowhere_in_the_database(self):
        secret = "Ein-ganz-eigenes-Passwort-42"
        user = User.objects.get(username="sek")
        user.set_password(secret)
        user.save()
        self.client.post(reverse("login"), {"username": "sek", "password": secret, "remember": "1"})
        self.client.post(reverse("login"), {"username": "sek", "password": secret + "x", "remember": "1"})
        for event in AuditEvent.objects.all():
            self.assertNotIn(secret, f"{event.summary} {event.target_id}")


@mock.patch.dict("os.environ", CLIENT_ENV)
@override_settings(ALLOWED_HOSTS=["localhost"], CSRF_TRUSTED_ORIGINS=[SIDECAR])
class ShellSignInTests(AppTestCase):
    """The shell's own sign-in when a database opens, with Django's CSRF check on."""

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True, HTTP_HOST="localhost")

    def sign_in(self, username, password):
        page = self.client.get(reverse("login"), secure=True, HTTP_ACCEPT="text/html")
        self.assertEqual(page.status_code, 200)
        token = TOKEN.search(page.content.decode()).group(1)
        return self.client.post(reverse("login"), {"csrfmiddlewaretoken": token, "username": username, "password": password},
                                secure=True, HTTP_ORIGIN=SIDECAR)

    def test_signs_in_so_the_window_opens_signed_in(self):
        answer = self.sign_in("sek", PASSWORD)
        self.assertEqual(answer.headers["X-DataFluss-Login"], "ok")
        self.assertEqual(self.client.session["_auth_user_id"], str(self.sek.pk))
        self.assertFalse(self.client.get("/", secure=True).get("Location", "").startswith(reverse("login") + "?"))

    def test_a_changed_password_is_failed(self):
        self.assertEqual(self.sign_in("sek", "the old one").headers["X-DataFluss-Login"], "failed")

    def test_the_second_factor_is_still_asked_for(self):
        User.objects.filter(username="sek").update(totp_secret="x", totp_confirmed=True)
        self.assertEqual(self.sign_in("sek", PASSWORD).headers["X-DataFluss-Login"], "ok")
        start = self.client.get("/", secure=True)
        self.assertRedirects(start, reverse("totp_verify"), fetch_redirect_response=False)

    def test_signed_in_already_gives_no_page_to_fill(self):
        """Sidecar::sign_in then leaves the session alone."""
        self.sign_in("sek", PASSWORD)
        self.assertEqual(self.client.get(reverse("login"), secure=True).status_code, 302)
