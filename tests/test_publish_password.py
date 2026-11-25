"""A password in front of a site on this server (publish/guard.py)."""
from django.urls import reverse

from publish import guard, service
from publish.models import Publication

from .test_publish import Synchronous
from .test_publish_safety import PublishCase


class PasswordTests(Synchronous, PublishCase):
    def setUp(self):
        super().setUp()
        Publication.objects.filter(pk=self.publication.pk).update(target=Publication.SERVER, password=guard.hashed("Kino-Abend-7"))
        service.start(self.confirmed(), service.PUBLISH)
        self.assertEqual(Publication.objects.get().status, Publication.DONE)

    def files(self):
        return ["/p/filmabend/", f"/p/filmabend/filme/{self.momo.pk}.html", "/p/filmabend/_assets/search.js", "/p/filmabend/robots.txt",
                *[f"/p/filmabend/{p}" for p in Publication.objects.get().manifest["files"] if p.startswith("_media/")][:1]]

    def test_nothing_without_the_password(self):
        for url in self.files():
            with self.subTest(url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                body = response.content.decode()
                self.assertIn('name="password"', body)
                self.assertNotIn("Momo", body)
                self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")

    def test_the_password_opens_it_and_a_new_one_closes_it(self):
        wrong = self.client.post("/p/filmabend/", {"password": "falsch"})
        self.assertEqual(wrong.status_code, 401)
        right = self.client.post(f"/p/filmabend/filme/{self.momo.pk}.html", {"password": "Kino-Abend-7"})
        self.assertRedirects(right, f"/p/filmabend/filme/{self.momo.pk}.html", fetch_redirect_response=False)
        cookie = right.cookies[guard.cookie_name("filmabend")]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["path"], "/p/filmabend/")
        page = self.client.get(f"/p/filmabend/filme/{self.momo.pk}.html")
        self.assertIn("Momo", b"".join(page.streaming_content).decode())
        self.assertEqual(page["Cache-Control"], "private, no-cache")
        self.assertEqual(page["X-Robots-Tag"], "noindex, nofollow")  # protected sites are never listed

        Publication.objects.filter(pk=self.publication.pk).update(password=guard.hashed("Neues-Passwort-8"))
        self.assertIn('name="password"', self.client.get("/p/filmabend/").content.decode())

    def test_ten_failures_lock_the_address(self):
        for _n in range(guard.MAX_FAILURES):
            self.client.post("/p/filmabend/", {"password": "falsch"})
        locked = self.client.post("/p/filmabend/", {"password": "Kino-Abend-7"})
        self.assertIn("Zu viele Fehlversuche", locked.content.decode())
        self.assertNotIn(guard.cookie_name("filmabend"), locked.cookies)

    def test_set_and_remove_in_the_settings(self):
        self.login(self.it)
        url = reverse("publication_detail", args=[self.publication.pk])
        self.client.post(url, {"section": "target", "target": "server", "protect": "1"})  # keeps the stored one
        self.assertTrue(Publication.objects.get().password)
        self.client.post(url, {"section": "target", "target": "server"})
        self.assertEqual(Publication.objects.get().password, "")
        self.assertIn("Momo", b"".join(self.client.get(f"/p/filmabend/filme/{self.momo.pk}.html").streaming_content).decode())
        refused = self.client.post(url, {"section": "target", "target": "server", "protect": "1"}, follow=True)
        self.assertIn("Bitte ein Passwort", refused.content.decode())
        self.client.post(url, {"section": "target", "target": "server", "protect": "1", "site_password": "Drittes-9"})
        stored = Publication.objects.get().password
        self.assertTrue(stored.startswith(("argon2", "pbkdf2")))
        self.assertNotIn("Drittes-9", stored)
