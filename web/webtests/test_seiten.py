"""Websites on the sites' own host (seiten/, docs/website.md "Websites"): asking for them, the reviewer's
decision, the hosting API with a token, serving in a sandbox on that host only, reporting and switching off."""
import re
import shutil
import tempfile

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from konten.decorators import TOTP_OK
from konten.models import User
from publish import hosting as protocol
from seiten.models import Access, Site, SiteReport

from .test_site import human

MEDIA = tempfile.mkdtemp(prefix="web-seiten-")
SITES = "seiten.localhost:8140"


@override_settings(MEDIA_ROOT=MEDIA, REVIEW_NOTIFY=["pruefer@example.org"], SITES_URL=f"http://{SITES}", SITES_QUOTA_MB=1, SITES_PER_ACCOUNT=2)
class SitesCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.anna = User.objects.create_user("anna@example.org", "Anna")
        self.prue = User.objects.create_user("pruefer@example.org", "Prüferin", is_staff=True)

    def as_reviewer(self):
        self.client.force_login(self.prue)
        session = self.client.session
        session[TOTP_OK] = True
        session.save()

    def granted_site(self, name="Filmclub"):
        Access.objects.create(user=self.anna, reason="Unsere Filmliste", status=Access.GRANTED)
        self.client.force_login(self.anna)
        page = self.client.post(reverse("sites_action"), {"action": "create", "name": name}).content.decode()
        token = re.search(r'value="([^"]+)" readonly data-token', page).group(1)
        self.client.logout()
        return Site.objects.get(name=name), token

    def api(self, method, path, token, body=b""):
        return getattr(self.client, method)(f"/api/hosting/v1/{path}", body, content_type="application/octet-stream",
                                             HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_HOST=SITES)


class AccessTests(SitesCase):
    def test_only_after_a_reviewer_unlocks_the_account(self):
        self.client.force_login(self.anna)
        self.client.post(reverse("sites_action"), {"action": "create", "name": "Zu früh"})
        self.assertFalse(Site.objects.exists())
        self.client.post(reverse("sites_ask"), {"reason": "Die Filmliste unseres Vereins"})
        self.assertEqual(Access.objects.get().status, Access.REQUESTED)
        self.assertIn("Antrag", mail.outbox[-1].subject)

        self.as_reviewer()
        self.assertContains(self.client.get(reverse("sites_queue")), "Die Filmliste unseres Vereins")
        self.client.post(reverse("sites_decide", args=[Access.objects.get().pk]), {"action": "refuse"})  # needs a reason
        self.assertEqual(Access.objects.get().status, Access.REQUESTED)
        self.client.post(reverse("sites_decide", args=[Access.objects.get().pk]), {"action": "grant"})
        self.assertEqual(Access.objects.get().status, Access.GRANTED)
        self.assertEqual(mail.outbox[-1].to, ["anna@example.org"])

        self.client.force_login(self.anna)
        page = self.client.post(reverse("sites_action"), {"action": "create", "name": "Filmclub"}).content.decode()
        self.assertIn("nur dieses eine Mal", page)
        token = re.search(r'value="([^"]+)" readonly data-token', page).group(1)
        self.assertEqual(Site.objects.get().token_hash, protocol.token_hash(token))
        self.assertNotIn(token, self.client.get(reverse("websites")).content.decode())

    def test_a_limit_per_account(self):
        self.granted_site("Eins")
        self.client.force_login(self.anna)
        self.client.post(reverse("sites_action"), {"action": "create", "name": "Zwei"})
        self.client.post(reverse("sites_action"), {"action": "create", "name": "Drei"})
        self.assertEqual(Site.objects.count(), 2)


class HostingTests(SitesCase):
    def test_publish_and_serve_in_a_sandbox_on_the_sites_host_only(self):
        site, token = self.granted_site()
        info = self.api("get", "site", token).json()
        self.assertEqual(info["url"], f"http://{SITES}/{site.slug}/")
        self.assertEqual(self.api("put", "files/index.html", token, b"<h1>Filmclub</h1>").status_code, 201)
        page = self.client.get(f"/{site.slug}/", HTTP_HOST=SITES)
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Filmclub", b"".join(page.streaming_content))
        self.assertIn("sandbox allow-scripts", page["Content-Security-Policy"])
        self.assertFalse(page.cookies)  # no session or CSRF cookie on the sites' host
        self.assertEqual(self.client.get(f"/{site.slug}/").status_code, 404)  # never on the template site's host
        self.assertEqual(self.client.get("/api/hosting/v1/site", HTTP_AUTHORIZATION=f"Bearer {token}").status_code, 404)
        self.assertEqual(self.api("delete", "files/index.html", token).status_code, 200)
        self.assertEqual(self.client.get(f"/{site.slug}/", HTTP_HOST=SITES).status_code, 404)

    def test_refusals_quota_and_switching_off(self):
        site, token = self.granted_site()
        self.assertEqual(self.api("get", "site", "falsch").status_code, 401)
        self.assertEqual(self.api("put", "files/../x", token, b"x").status_code, 400)
        self.assertEqual(self.api("put", "files/gross.bin", token, b"x" * (1024 * 1024 + 1)).status_code, 507)
        self.api("put", "files/index.html", token, b"ok")
        site.disabled = True
        site.save()
        self.assertEqual(self.client.get(f"/{site.slug}/", HTTP_HOST=SITES).status_code, 404)
        self.assertEqual(self.api("get", "site", token).status_code, 401)
        site.disabled = False
        site.save()
        Access.objects.filter(user=self.anna).update(status=Access.BLOCKED)
        self.assertEqual(self.client.get(f"/{site.slug}/", HTTP_HOST=SITES).status_code, 404)
        self.assertEqual(self.api("put", "files/index.html", token, b"neu").status_code, 401)

    def test_report_and_switch_off_by_a_reviewer(self):
        site, _token = self.granted_site()
        self.client.post(reverse("sites_report"), human({"address": f"http://{SITES}/{site.slug}/", "reason": "Werbung"}))
        report = SiteReport.objects.get()
        self.assertEqual(report.site, site)
        self.as_reviewer()
        self.assertContains(self.client.get(reverse("review_queue")), "Websites prüfen")
        self.client.post(reverse("sites_switch", args=[site.pk]), {"action": "off", "note": "Werbung"})
        site.refresh_from_db()
        self.assertTrue(site.disabled)
        self.assertTrue(SiteReport.objects.get().handled)
        self.client.logout()
        self.assertEqual(self.client.get(f"/{site.slug}/", HTTP_HOST=SITES).status_code, 404)

    def test_the_owner_deletes_a_site_with_its_files(self):
        site, token = self.granted_site()
        self.api("put", "files/index.html", token, b"ok")
        self.client.force_login(self.anna)
        self.client.post(reverse("sites_action"), {"action": "delete", "site": site.pk})
        self.assertFalse(Site.objects.exists())
        self.assertEqual(self.api("get", "site", token).status_code, 401)


class DeployCheckTests(TestCase):
    def test_the_sites_host_must_be_its_own(self):
        from seiten.checks import sites_ready

        with override_settings(SITE_URL="https://vorlagen.datafluss.de", SITES_URL="https://seiten.datafluss.de"):
            self.assertEqual(sites_ready(None), [])
        with override_settings(SITE_URL="https://vorlagen.datafluss.de", SITES_URL="https://vorlagen.datafluss.de"):
            self.assertEqual([e.id for e in sites_ready(None)], ["web.E102"])
        with override_settings(SITE_URL="https://vorlagen.datafluss.de", SITES_URL="http://seiten.datafluss.de", SESSION_COOKIE_DOMAIN=".datafluss.de"):
            self.assertEqual([e.id for e in sites_ready(None)], ["web.E101", "web.E103"])
