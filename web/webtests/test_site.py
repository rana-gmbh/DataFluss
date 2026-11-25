"""The template site (docs/website.md): signing in by link, sharing, reviewing, rating, the API and the limits."""
import copy
import hashlib
import json
import re
import shutil
import tempfile
from decimal import Decimal
from io import BytesIO, StringIO

from django.core import mail, signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
import pyotp
from PIL import Image

from katalog import services
from katalog.models import Picture, Report, Review, Template, Version
from konten import limits, tokens
from konten.decorators import TOTP_OK
from konten.models import User
from tables import template_check as tc

INVENTORY = json.loads((tc.LIBRARY / "inventar.json").read_text(encoding="utf-8"))
MEDIA = tempfile.mkdtemp(prefix="web-media-")


def template_file(**changes):
    data = {**copy.deepcopy(INVENTORY), "id": "mein_inventar", "title": "Mein Inventar", **changes}
    return json.dumps(data).encode()


def png(color="red", size=(40, 30)):
    out = BytesIO()
    image = Image.new("RGB", size, color)
    image.save(out, "PNG", pnginfo=None)
    return SimpleUploadedFile("bild.png", out.getvalue(), content_type="image/png")


def human(data=None):
    """A form as a person sends it: with its stamp from a while ago."""
    stamp = signing.dumps(0, salt=limits.FORM_SALT)
    return {"stamp": stamp, **(data or {})}


@override_settings(MEDIA_ROOT=MEDIA, REVIEW_NOTIFY=["pruefer@example.org"])
class SiteCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.anna = User.objects.create_user("anna@example.org", "Anna")
        self.ben = User.objects.create_user("ben@example.org", "Ben")
        self.prue = User.objects.create_user("pruefer@example.org", "Prüferin", is_staff=True)

    def as_reviewer(self):
        self.client.force_login(self.prue)
        session = self.client.session
        session[TOTP_OK] = True
        session.save()

    def shared(self, user=None, **changes):
        version, problems = services.submit(user or self.anna, template_file(**changes), "arbeit")
        self.assertEqual(problems, [])
        return version

    def published(self, **changes):
        version = self.shared(**changes)
        services.approve(version, self.prue)
        return Template.objects.get(pk=version.template_id)


class SignInTests(SiteCase):
    def link_from_mail(self):
        return re.search(r"http\S+/anmelden/link/\S+", mail.outbox[-1].body).group(0)

    def test_a_link_signs_in_once_and_creates_the_account_on_first_use(self):
        answer = self.client.post(reverse("login"), human({"email": "Neu@Example.org"}))
        self.assertContains(answer, "Postfach")
        self.assertFalse(User.objects.filter(email="neu@example.org").exists())  # not before the link is used
        link = self.link_from_mail().replace("http://127.0.0.1:8140", "")
        self.assertContains(self.client.get(link), "Jetzt anmelden")  # a mail scanner opening it changes nothing
        answer = self.client.post(link)
        self.assertRedirects(answer, reverse("account") + "?next=/", fetch_redirect_response=False)
        user = User.objects.get(email="neu@example.org")
        self.assertFalse(user.has_usable_password())
        self.client.post(reverse("logout"))
        self.assertEqual(self.client.post(link).status_code, 400)  # spent

    def test_the_same_answer_for_known_and_unknown_addresses_and_limits(self):
        for email in ("anna@example.org", "niemand@example.org"):
            self.assertContains(self.client.post(reverse("login"), human({"email": email})), "Wenn die Adresse")
        for _ in range(4):
            self.client.post(reverse("login"), human({"email": "anna@example.org"}))
        self.assertContains(self.client.post(reverse("login"), human({"email": "anna@example.org"})), "Zu viele")

    def test_bots_are_turned_away(self):
        self.assertContains(self.client.post(reverse("login"), human({"email": "a@example.org", "website": "spam"})), "zu schnell")
        fresh = {"email": "a@example.org", "stamp": limits.form_stamp()}
        self.assertContains(self.client.post(reverse("login"), fresh), "zu schnell")
        self.assertEqual(len(mail.outbox), 0)

    def test_tokens_expire_and_cannot_be_forged(self):
        self.assertIsNone(tokens.read("nonsense"))
        early = tokens.make("anna@example.org", self.anna)
        self.assertEqual(tokens.read(early), "anna@example.org")
        self.client.force_login(self.anna)  # signing in spends every earlier link
        self.assertIsNone(tokens.read(early))
        self.assertIsNone(tokens.read(tokens.make("anna@example.org", None)))

    def test_reviewers_need_a_second_factor(self):
        self.client.force_login(self.prue)
        self.assertRedirects(self.client.get(reverse("review_queue")), reverse("totp") + "?next=/pruefen/", fetch_redirect_response=False)
        page = self.client.get(reverse("totp"))
        secret = page.context["secret"]
        self.assertContains(self.client.post(reverse("totp"), {"code": "000000"}), "stimmt nicht")
        self.client.post(reverse("totp"), {"code": pyotp.TOTP(secret).now()})
        self.prue.refresh_from_db()
        self.assertEqual(self.prue.totp_secret, secret)
        self.assertEqual(self.client.get(reverse("review_queue")).status_code, 200)
        self.client.force_login(self.anna)
        self.assertEqual(self.client.get(reverse("review_queue")).status_code, 403)


class ShareAndReviewTests(SiteCase):
    def test_upload_waits_for_review_and_appears_once_approved(self):
        self.client.force_login(self.anna)
        answer = self.client.post(reverse("upload"), human({"file": SimpleUploadedFile("t.json", template_file()), "category": "arbeit",
                                                           "license": "1", "no_personal_data": "1", "pictures": [png(), png("blue")]}))
        self.assertRedirects(answer, reverse("my_templates"))
        version = Version.objects.get()
        self.assertEqual((version.status, version.number, version.template.owner), (Version.SUBMITTED, 1, self.anna))
        self.assertEqual(version.pictures.count(), 2)
        self.assertIn("pruefer@example.org", mail.outbox[-1].to)
        self.client.logout()
        self.assertNotContains(self.client.get(reverse("home")), "Mein Inventar")
        self.assertEqual(self.client.get(reverse("template_detail", args=["mein_inventar"])).status_code, 404)
        picture = version.pictures.first()
        self.assertEqual(self.client.get(reverse("picture", args=[picture.uid])).status_code, 404)  # not yet public
        self.as_reviewer()
        self.assertContains(self.client.get(reverse("review_version", args=[version.pk])), "Gegenstände")
        self.client.post(reverse("review_version", args=[version.pk]), {"action": "approve"})
        version.refresh_from_db()
        self.assertEqual(version.status, Version.APPROVED)
        self.assertIn("freigegeben", mail.outbox[-1].subject)
        self.client.logout()
        self.assertContains(self.client.get(reverse("home")), "Mein Inventar")
        self.assertContains(self.client.get(reverse("home"), {"q": "gegenst"}), "Mein Inventar")  # table names are searched
        self.assertNotContains(self.client.get(reverse("home"), {"q": "rezepte"}), "Mein Inventar")
        image = self.client.get(reverse("picture", args=[picture.uid]))
        self.assertEqual(image["Content-Type"], "image/webp")

    def test_refused_files_say_why(self):
        self.client.force_login(self.anna)
        hostile = json.loads(template_file())
        hostile["tables"][0]["fields"][0]["config"] = {"target": "pupil"}
        hostile["masks"][0]["tree"]["tabs"][0]["id"] = "x');alert(1);('"
        for raw, words in ((json.dumps(hostile).encode(), "ungültige Kennung"), (b"kein json", "kein JSON"),
                           (template_file(id="filme"), "vergeben")):
            answer = self.client.post(reverse("upload"), human({"file": SimpleUploadedFile("t.json", raw), "license": "1", "no_personal_data": "1"}))
            self.assertContains(answer, words)
        self.assertFalse(Version.objects.exists())
        answer = self.client.post(reverse("upload"), human({"file": SimpleUploadedFile("t.json", template_file())}))
        self.assertContains(answer, "Bedingungen")

    def test_ids_belong_to_their_owner_and_versions_only_go_up(self):
        template = self.published()
        _version, problems = services.submit(self.ben, template_file(), "arbeit")
        self.assertIn("vergeben", " ".join(problems))
        _version, problems = services.submit(self.anna, template_file(), "arbeit", template=template)
        self.assertIn("größer", " ".join(problems))
        second, problems = services.submit(self.anna, template_file(version=2), "arbeit", changes="Neues Feld", template=template)
        self.assertEqual(problems, [])
        template.refresh_from_db()
        self.assertEqual(template.published.number, 1)  # the shown one stays until the new one is approved
        services.approve(second, self.prue)
        template.refresh_from_db()
        self.assertEqual((template.published.number, template.versions.get(number=1).status), (2, Version.REPLACED))

    def test_rejecting_needs_a_reason_and_tells_the_owner(self):
        version = self.shared()
        self.as_reviewer()
        self.client.post(reverse("review_version", args=[version.pk]), {"action": "reject"})
        version.refresh_from_db()
        self.assertEqual(version.status, Version.SUBMITTED)
        self.client.post(reverse("review_version", args=[version.pk]), {"action": "reject", "decision": "Bitte ohne echte Namen."})
        version.refresh_from_db()
        self.assertEqual(version.status, Version.REJECTED)
        self.assertIn("Bitte ohne echte Namen.", mail.outbox[-1].body)
        self.assertEqual(mail.outbox[-1].to, ["anna@example.org"])

    def test_pictures_are_made_anew(self):
        version = self.shared()
        _v, problems = services.submit(self.anna, template_file(version=2), uploads=[SimpleUploadedFile("x.png", b"<svg onload=x>")], template=version.template)
        self.assertIn("kein lesbares Bild", " ".join(problems))
        big = png(size=(3000, 1000))
        second, problems = services.submit(self.anna, template_file(version=2), uploads=[big], template=version.template)
        picture = Picture.objects.get(version=second)
        self.assertEqual((picture.width, picture.height), (1600, 533))
        with picture.image.open("rb") as f:
            self.assertEqual(Image.open(f).format, "WEBP")


class RatingTests(SiteCase):
    def setUp(self):
        super().setUp()
        self.template = self.published()

    def rate(self, user, **data):
        self.client.force_login(user)
        return self.client.post(reverse("template_review", args=[self.template.key]), human(data))

    def test_half_stars_count_at_once_texts_after_review(self):
        self.rate(self.ben, stars="3.5", text="Sehr praktisch!")
        self.rate(self.prue, stars="5")
        self.template.refresh_from_db()
        self.assertEqual((self.template.rating, self.template.ratings), (Decimal("4.25"), 2))
        review = Review.objects.get(user=self.ben)
        self.assertEqual((review.stars, review.text_status), (Decimal("3.5"), Review.SUBMITTED))
        self.client.logout()
        self.assertNotContains(self.client.get(reverse("template_detail", args=[self.template.key])), "Sehr praktisch!")
        self.as_reviewer()
        self.client.post(reverse("review_text", args=[review.pk]), {"action": "approve"})
        self.client.logout()
        self.assertContains(self.client.get(reverse("template_detail", args=[self.template.key])), "Sehr praktisch!")
        self.rate(self.ben, stars="4", text="Sehr praktisch!")  # same text keeps its approval
        review.refresh_from_db()
        self.assertEqual((review.stars, review.text_status), (Decimal("4.0"), Review.APPROVED))
        self.rate(self.ben, stars="4", text="Geändert")
        review.refresh_from_db()
        self.assertEqual(review.text_status, Review.SUBMITTED)

    def test_only_half_steps_from_half_to_five_and_never_ones_own(self):
        for bad in ("0", "5.5", "3.3", "x", "-1"):
            self.rate(self.ben, stars=bad)
        self.assertFalse(Review.objects.exists())
        self.rate(self.anna, stars="5")
        self.assertFalse(Review.objects.exists())
        self.rate(self.ben, stars="0.5")
        self.assertEqual(Review.objects.get().stars, Decimal("0.5"))
        self.rate(self.ben, action="delete")
        self.template.refresh_from_db()
        self.assertEqual((self.template.ratings, self.template.rating), (0, Decimal("0")))

    def test_reports_reach_the_queue(self):
        self.client.force_login(self.ben)
        self.client.post(reverse("template_report", args=[self.template.key]), {"reason": "Werbung"})
        report = Report.objects.get()
        self.as_reviewer()
        self.assertContains(self.client.get(reverse("review_queue")), "Werbung")
        self.client.post(reverse("review_report", args=[report.pk]), {"action": "hide_template"})
        self.template.refresh_from_db()
        self.assertTrue(self.template.withdrawn)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("template_detail", args=[self.template.key])).status_code, 404)


class ApiAndDownloadTests(SiteCase):
    def test_index_and_file_for_datafluss(self):
        template = self.published()
        index = self.client.get(reverse("api_index")).json()
        self.assertEqual(index["format"], 1)
        entry = index["templates"][0]
        self.assertEqual((entry["id"], entry["version"], entry["author"], entry["license"]), ("mein_inventar", 1, "Anna", "CC-BY-4.0"))
        self.assertEqual((entry["changes"], entry["picture"]), ({}, ""))
        answer = self.client.get(entry["url"].replace("http://127.0.0.1:8140", ""))
        self.assertEqual(hashlib.sha256(answer.content).hexdigest(), entry["sha256"])
        data = json.loads(answer.content)
        self.assertEqual((data["database"], data["requires"], data["catalog"]["source"]), (True, [], "datafluss.de"))
        self.assertEqual(tc.check_untrusted(data), [])
        download = self.client.get(reverse("template_download", args=[template.key]))
        self.assertIn("attachment", download["Content-Disposition"])
        template.refresh_from_db()
        self.assertEqual(template.downloads, 2)

    def test_index_names_what_a_version_brings_and_its_first_picture(self):
        version, _problems = services.submit(self.anna, template_file(changes={"1": {"de": "Erste Fassung"}}), uploads=[png()])
        services.approve(version, self.prue)
        entry = self.client.get(reverse("api_index")).json()["templates"][0]
        self.assertEqual(entry["changes"], {"1": {"de": "Erste Fassung"}})
        self.assertTrue(entry["picture"].endswith(".webp"))

    def test_unpublished_and_withdrawn_are_not_handed_out(self):
        version = self.shared()
        self.assertEqual(self.client.get(reverse("api_index")).json()["templates"], [])
        self.assertEqual(self.client.get(reverse("api_file", args=["mein_inventar", 1])).status_code, 404)
        services.approve(version, self.prue)
        Template.objects.update(withdrawn=True)
        self.assertEqual(self.client.get(reverse("template_download", args=["mein_inventar"])).status_code, 404)

    def test_the_platforms_templates_become_official(self):
        from django.core.management import call_command

        call_command("import_library", stdout=StringIO())
        keys = set(Template.objects.filter(official=True, published__isnull=False).values_list("key", flat=True))
        self.assertEqual(keys, {"filme", "buecherei", "termine", "inventar"})
        self.assertEqual(Template.objects.get(key="filme").author, "DataFluss")

    def test_pages_carry_a_strict_policy(self):
        page = self.client.get(reverse("home"))
        self.assertIn("script-src 'self'", page["Content-Security-Policy"])
        self.assertNotIn("unsafe", page["Content-Security-Policy"])
        for name in ("imprint", "privacy", "terms", "healthz", "login"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)


class AccountTests(SiteCase):
    def test_deleting_an_account(self):
        template = self.published()
        services.submit(self.anna, template_file(version=2), template=template)
        never = services.submit(self.anna, template_file(id="nie_gezeigt"))[0].template
        services.rate(template, self.ben, Decimal("4"))
        services.rate(template, self.prue, Decimal("2"))
        self.client.force_login(self.anna)
        self.client.post(reverse("account"), {"action": "delete", "confirm": "falsch@example.org"})
        self.assertTrue(User.objects.filter(pk=self.anna.pk).exists())
        self.client.post(reverse("account"), {"action": "delete", "confirm": "anna@example.org"})
        self.assertFalse(User.objects.filter(pk=self.anna.pk).exists())
        template.refresh_from_db()
        self.assertEqual((template.author, template.listed), ("gelöschtes Konto", True))
        self.assertFalse(template.versions.filter(status=Version.SUBMITTED).exists())
        self.assertFalse(Template.objects.filter(pk=never.pk).exists())
        services.forget_user(self.ben)
        template.refresh_from_db()
        self.assertEqual((template.ratings, template.rating), (1, Decimal("2")))

    def test_a_name_comes_first(self):
        nameless = User.objects.create_user("x@example.org")
        self.client.force_login(nameless)
        self.assertRedirects(self.client.get(reverse("upload")), reverse("account") + "?next=/hochladen/", fetch_redirect_response=False)
        self.client.post(reverse("account"), {"action": "name", "name": "  Xaver  "})
        nameless.refresh_from_db()
        self.assertEqual(nameless.name, "Xaver")


class PageTests(SiteCase):
    def test_star_choices_are_plain_numbers_whatever_the_language(self):
        template = self.published()
        self.client.force_login(self.ben)
        page = self.client.get(reverse("template_detail", args=[template.key])).content.decode()
        self.assertIn('<option value="3.5"', page)
        self.assertNotIn('value="3,5"', page)


class LaunchTests(SiteCase):
    def test_english_from_the_switch_and_the_browser(self):
        self.assertContains(self.client.get(reverse("home"), HTTP_ACCEPT_LANGUAGE="en"), "Ready-made databases for DataFluss")
        self.client.get(reverse("language", args=["de"]) + "?next=/")
        self.assertContains(self.client.get(reverse("home"), HTTP_ACCEPT_LANGUAGE="en"), "Fertige Datenbanken für DataFluss")
        self.client.get(reverse("language", args=["en"]) + "?next=/")
        page = self.client.get(reverse("home"))
        self.assertContains(page, '<html lang="en">')
        self.assertContains(page, "Collections and household")  # the categories too

    def test_the_owner_hears_in_the_language_of_their_pages(self):
        self.client.force_login(self.anna)
        self.client.get(reverse("language", args=["en"]) + "?next=/")
        self.client.post(reverse("upload"), human({"file": SimpleUploadedFile("t.json", template_file()), "category": "arbeit",
                                                  "license": "1", "no_personal_data": "1"}))
        self.anna.refresh_from_db()
        self.assertEqual(self.anna.language, "en")
        services.reject(Version.objects.get(), self.prue, "Bitte ohne echte Namen.")
        self.assertEqual(mail.outbox[-1].subject, "Your template was not approved: Mein Inventar")
        self.assertIn("The reason:", mail.outbox[-1].body)

    def test_the_template_checks_messages_are_english_too(self):
        self.client.force_login(self.anna)
        self.client.get(reverse("language", args=["en"]) + "?next=/")
        answer = self.client.post(reverse("upload"), human({"file": SimpleUploadedFile("t.json", template_file(id="filme")),
                                                           "license": "1", "no_personal_data": "1"}))
        self.assertContains(answer, "is taken")

    def test_robots_sitemap_and_not_found(self):
        template = self.published()
        robots = self.client.get("/robots.txt").content.decode()
        self.assertIn("Disallow: /pruefen/", robots)
        self.assertIn("Sitemap: http://127.0.0.1:8140/sitemap.xml", robots)
        sitemap = self.client.get("/sitemap.xml").content.decode()
        self.assertIn(f"/vorlagen/{template.key}/</loc>", sitemap)
        with self.settings(DEBUG=False):
            missing = self.client.get("/vorlagen/gibt_es_nicht/")
        self.assertEqual(missing.status_code, 404)
        self.assertContains(missing, "Diese Seite gibt es nicht", status_code=404)

    def test_a_published_page_tells_link_previews_its_picture(self):
        version, _problems = services.submit(self.anna, template_file(), uploads=[png()])
        services.approve(version, self.prue)
        page = self.client.get(reverse("template_detail", args=["mein_inventar"]))
        self.assertContains(page, '<meta property="og:title" content="Mein Inventar">')
        self.assertContains(page, 'property="og:image"')

    def test_the_deploy_checks_name_what_is_missing(self):
        from konten.checks import site_ready

        ids = {c.id for c in site_ready(None)}
        self.assertTrue({"web.E001", "web.E004", "web.E005"} <= ids)
        good = {"SITE_URL": "https://templates.datafluss.de", "DEBUG": False, "SECRET_KEY": "x" * 60, "EMAIL_HOST": "smtp.example",
                "TRUST_PROXY": True, "REVIEW_NOTIFY": ["a@example.org"],
                "DATABASES": {"default": {"ENGINE": "django.db.backends.postgresql"}}}
        with self.settings(**good):
            self.assertEqual(site_ready(None), [])

    def test_titles_and_numbers_follow_the_language(self):
        template = self.published()
        services.rate(template, self.ben, Decimal("3.5"))
        english = self.client.get(reverse("home"), HTTP_ACCEPT_LANGUAGE="en").content.decode()
        self.assertIn("Inventory", english)  # the template's own English title (titles.en)
        self.assertIn("3.5 (1)", english)
        german = self.client.get(reverse("home"), HTTP_ACCEPT_LANGUAGE="de").content.decode()
        self.assertIn("Mein Inventar", german)
        self.assertIn("3,5 (1)", german)


class PrivacyAndAdminTests(SiteCase):
    def test_browsing_sets_no_cookie_until_someone_switches_the_language(self):
        template = self.published()
        for url in (reverse("home"), reverse("template_detail", args=[template.key]), reverse("privacy")):
            answer = self.client.get(url)
            self.assertEqual(dict(answer.cookies), {}, url)
        page = self.client.get(reverse("home")).content.decode()
        self.assertIn(">Deutsch</a>", page)
        self.assertIn(">English</a>", page)  # each language in its own name
        answer = self.client.get(reverse("language", args=["en"]) + "?next=/vorlagen/" + template.key + "/")
        self.assertRedirects(answer, reverse("template_detail", args=[template.key]), fetch_redirect_response=False)
        self.assertEqual(list(answer.cookies), ["dfweb_language"])
        self.assertTrue(answer.cookies["dfweb_language"]["httponly"])
        away = self.client.get(reverse("language", args=["en"]) + "?next=https://evil.example/")
        self.assertEqual(away["Location"], "/")

    def test_createsuperuser_asks_for_no_password(self):
        from io import StringIO

        from django.core.management import call_command

        call_command("createsuperuser", "--noinput", "--email", "Neu@Example.org", "--name", "Neue Prüferin", stdout=StringIO())
        user = User.objects.get(email="neu@example.org")
        self.assertEqual((user.is_staff, user.name, user.has_usable_password()), (True, "Neue Prüferin", False))
        call_command("createsuperuser", "--noinput", "--email", "ben@example.org", stdout=StringIO())
        self.ben.refresh_from_db()
        self.assertTrue(self.ben.is_staff)
