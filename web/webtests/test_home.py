"""datafluss.de, the main page (home/): its own host on the same site, no cookies, the newest release from GitHub."""
import json
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from home import github
from home.checks import home_ready

HOST = "datafluss.localhost:8140"
RELEASE = {"version": "1.3.1", "date": "2026-11-18", "notes": "https://github.com/rana-gmbh/DataFluss/releases/tag/v1.3.1",
           "mac": "https://github.com/rana-gmbh/DataFluss/releases/download/v1.3.1/DataFluss-1.3.1.dmg",
           "windows": "https://github.com/rana-gmbh/DataFluss/releases/download/v1.3.1/DataFluss-1.3.1-setup.exe"}


class HomeTests(TestCase):
    def setUp(self):
        cache.clear()

    def get(self, path="/", **headers):
        return self.client.get(path, HTTP_HOST=HOST, **headers)

    def test_the_main_page_answers_on_its_own_host_without_cookies(self):
        with mock.patch.object(github, "_fetch", return_value=RELEASE):
            page = self.get(HTTP_ACCEPT_LANGUAGE="de")
        self.assertContains(page, "Die eigene Datenbank.")
        self.assertContains(page, RELEASE["mac"])
        self.assertContains(page, RELEASE["windows"])
        self.assertContains(page, "Version 1.3.1")
        self.assertEqual(dict(page.cookies), {})
        self.assertIn("script-src 'self'", page["Content-Security-Policy"])
        english = self.get(HTTP_ACCEPT_LANGUAGE="en")
        self.assertContains(english, "Your own database.")
        # The template site keeps its own start page.
        self.assertContains(self.client.get("/", HTTP_ACCEPT_LANGUAGE="de"), "Fertige Datenbanken für DataFluss")

    def test_github_away_links_to_the_release_page(self):
        with mock.patch.object(github, "_fetch", side_effect=OSError("offline")):
            page = self.get()
        self.assertContains(page, github.RELEASES + "/latest")
        self.assertNotContains(page, "releases/download")

    def test_the_release_is_asked_once_an_hour(self):
        with mock.patch.object(github, "_fetch", return_value=RELEASE) as fetch:
            self.get()
            self.get()
        self.assertEqual(fetch.call_count, 1)

    def test_only_real_github_download_links_are_used(self):
        answer = {"tag_name": "v9.9.9", "published_at": "2027-01-01T00:00:00Z",
                  "assets": [{"name": "DataFluss-9.9.9.dmg", "browser_download_url": "https://evil.example/x.dmg"}]}
        with mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(answer).encode()
            found = github._fetch()
        self.assertEqual((found["version"], found["mac"]), ("9.9.9", ""))

    def test_www_redirects_and_the_catalog_urls_stay_on_their_host(self):
        answer = self.client.get("/vorlagen/x/", HTTP_HOST="www." + HOST)
        self.assertEqual((answer.status_code, answer["Location"]), (301, "http://datafluss.localhost:8140/vorlagen/x/"))
        self.assertEqual(self.get("/hochladen/").status_code, 404)  # the catalog lives on its own host
        self.assertEqual(self.get("/robots.txt").status_code, 200)
        self.assertEqual(self.get("/healthz/").status_code, 200)

    def test_the_language_links_work_here_too(self):
        answer = self.get("/sprache/en/?next=/")
        self.assertEqual((answer.status_code, answer["Location"]), (302, "/"))
        self.assertEqual(list(answer.cookies), ["dfweb_language"])

    def test_deploy_check(self):
        self.assertTrue({c.id for c in home_ready(None)} >= {"web.E201"})
        with override_settings(HOME_URL="https://datafluss.de", SITE_URL="https://templates.datafluss.de",
                               SITES_URL="https://databases.datafluss.de"):
            self.assertEqual(home_ready(None), [])
        with override_settings(HOME_URL="https://templates.datafluss.de", SITE_URL="https://templates.datafluss.de"):
            self.assertIn("web.E202", {c.id for c in home_ready(None)})
