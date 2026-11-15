import gzip
import shutil
import tempfile
from io import BytesIO
from pathlib import Path

from django.core.files.base import ContentFile
from django.test import override_settings
from django.urls import reverse
from PIL import Image

from core import audit, sealed
from core.models import AuditEvent, Role, User
from devices.models import Asset, AssetCategory, SupportCase
from people.models import ClassGroup, Guardian, Person
from vault.models import Platform
from vault.services import generate_login

from .base import AppTestCase


class SearchTests(AppTestCase):
    def search(self, text):
        return self.client.get(reverse("global_search"), {"q": text})

    def test_two_words_find_a_parent(self):
        self.login(self.sek)
        Guardian.objects.create(first_name="Anna", last_name="Müller")
        self.assertContains(self.search("Anna Müller"), "Eltern")

    def test_classes_are_found(self):
        self.login(self.sek)
        self.assertContains(self.search("5a"), reverse("class_detail", args=[self.group.pk]))

    def test_damage_cases_are_found(self):
        self.login(self.it)
        asset = Asset.objects.create(number="NB-77")
        SupportCase.objects.create(asset=asset, description="Display")
        self.assertContains(self.search("NB-77"), "Schaden")


class DeviceHandoverTests(AppTestCase):
    def setUp(self):
        self.login(self.it)
        self.asset = Asset.objects.create(number="NB-1")

    def issue(self, name):
        return self.client.post(reverse("asset_action", args=[self.asset.pk]), {"action": "issue", "person_name": name}, follow=True)

    def test_clear_name_issues(self):
        self.issue("Lena Muster")
        self.assertEqual(self.asset.open_assignment.person, self.pupil)

    def test_same_surname_is_refused_with_the_choices(self):
        Person.objects.create(kind="pupil", first_name="Paul", last_name="Muster")
        response = self.issue("Muster")
        self.assertContains(response, "Mehrere Treffer")
        self.assertContains(response, "Paul Muster")
        self.assertIsNone(self.asset.open_assignment)

    def test_surname_of_several_words_works(self):
        person = Person.objects.create(kind="pupil", first_name="Ana", last_name="de la Cruz")
        self.issue("de la Cruz")
        self.assertEqual(self.asset.open_assignment.person, person)

    def test_name_as_the_refusal_shows_it_works(self):
        person = Person.objects.create(kind="pupil", first_name="Xiaoming", last_name="Wang", family_name_first=True)
        self.issue(person.display_name)
        self.assertEqual(self.asset.open_assignment.person, person)

    def test_blank_name_is_not_a_crash(self):
        self.assertEqual(self.issue("   ").status_code, 200)

    def test_nobody_found_says_so(self):
        self.assertContains(self.issue("Niemand"), "Niemand gefunden")

    def test_email_still_works(self):
        self.issue("lena@schule.example")
        self.assertEqual(self.asset.open_assignment.person, self.pupil)

    def test_return_sell_retire_and_damage(self):
        self.issue("Lena Muster")
        url = reverse("asset_action", args=[self.asset.pk])
        self.client.post(url, {"action": "toggle_damaged"})
        self.client.post(url, {"action": "return"})
        self.client.post(url, {"action": "retire"})
        self.asset.refresh_from_db()
        self.assertTrue(self.asset.damaged)
        self.assertTrue(self.asset.retired)
        self.assertIsNone(self.asset.open_assignment)

    def test_only_editors_may(self):
        self.login(self.sl)
        self.assertEqual(self.client.post(reverse("asset_action", args=[self.asset.pk]), {"action": "return"}).status_code, 403)


class BulkTests(AppTestCase):
    def setUp(self):
        self.login(self.it)
        self.other = Person.objects.create(kind="pupil", first_name="Tom", last_name="Test")

    def run_action(self, **payload):
        return self.client.post(reverse("bulk_action"), {"ids": [str(self.pupil.pk), str(self.other.pk)], **payload}, content_type="application/json")

    def test_class(self):
        self.assertEqual(self.run_action(action="class", value=str(self.top.pk)).json()["count"], 2)
        self.other.refresh_from_db()
        self.assertEqual(self.other.current_class, self.top)

    def test_archive_and_restore(self):
        self.run_action(action="archive")
        self.pupil.refresh_from_db()
        self.assertTrue(self.pupil.archived)
        self.assertIsNotNone(self.pupil.exit_date)
        self.run_action(action="restore")
        self.pupil.refresh_from_db()
        self.assertFalse(self.pupil.archived)

    def test_logins(self):
        response = self.run_action(action="logins", value=str(Platform.objects.get(key="m365").pk))
        self.assertEqual(response.status_code, 200)

    def test_read_only_roles_cannot(self):
        self.login(self.sl)
        self.assertEqual(self.run_action(action="archive").status_code, 403)


class ReadOnlyTests(AppTestCase):
    def test_management_sees_locked_fields(self):
        self.login(self.sl)
        guardian = Guardian.objects.create(first_name="A", last_name="B")
        self.assertContains(self.client.get(reverse("guardian_detail", args=[guardian.pk])), "disabled")

    def test_management_gets_no_create_forms(self):
        self.login(self.sl)
        self.assertNotContains(self.client.get(reverse("asset_categories")), "Neuer Typ")

    def test_staff_can_still_edit(self):
        self.login(self.sek)
        guardian = Guardian.objects.create(first_name="A", last_name="B")
        self.assertNotContains(self.client.get(reverse("guardian_detail", args=[guardian.pk])), "disabled>")


class PagingTests(AppTestCase):
    def test_every_parent_can_be_reached(self):
        self.login(self.sek)
        Guardian.objects.bulk_create(Guardian(first_name="A", last_name=f"N{i:03}") for i in range(120))
        response = self.client.get(reverse("guardians"), {"seite": 3})
        self.assertContains(response, "N119")
        self.assertContains(response, "120 Einträge")

    def test_logins_page_in_order(self):
        self.login(self.it)
        for index in range(60):
            generate_login(Person.objects.create(kind="pupil", first_name="A", last_name=f"N{index:03}"), Platform.objects.get(key="m365"))
        response = self.client.get(reverse("platform_list", args=["m365"]))
        self.assertContains(response, "Seite 1 von 2")


class WordingTests(AppTestCase):
    def test_log_uses_plain_german(self):
        self.login(self.it)
        audit.log("login_failed", "x")
        response = self.client.get(reverse("audit_log"))
        self.assertContains(response, "Anmeldung fehlgeschlagen")
        self.assertNotContains(response, ">login_failed<")

    def test_menu_has_one_meaning_for_each_word(self):
        self.login(self.it)
        titles = [item[0] for _, items in self.client.get(reverse("pupils")).context["nav"] for item in items]
        self.assertIn("Druckvorlagen", titles)

    def test_management_goes_straight_to_the_log(self):
        self.login(self.sl)
        self.assertRedirects(self.client.get(reverse("settings_home")), reverse("audit_log"))


class StartPageTests(AppTestCase):
    def test_management_is_spared_it_tasks(self):
        self.login(self.sl)
        self.assertNotContains(self.client.get("/"), "Neue Codes")

    def test_empty_school_gets_first_steps(self):
        Person.objects.all().delete()
        self.login(self.it)
        self.assertContains(self.client.get("/"), "Erste Schritte")


class PanelTests(AppTestCase):
    def test_first_tab_needs_no_second_request(self):
        self.login(self.sek)
        html = self.client.get(reverse("person_panel", args=[self.pupil.pk])).content.decode()
        first_tab = html.split('role="tabpanel"')[1]
        self.assertNotIn('hx-trigger="intersect once"', first_tab)


class CompressionTests(AppTestCase):
    def test_pages_are_compressed_when_the_browser_can_read_it(self):
        self.login(self.sek)
        response = self.client.get(reverse("guardians"), headers={"Accept-Encoding": "gzip"})
        self.assertEqual(response["Content-Encoding"], "gzip")
        self.assertIn(b"Eltern", gzip.decompress(response.content))


class ThumbnailTests(AppTestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        buffer = BytesIO()
        Image.new("RGB", (800, 1000), "red").save(buffer, "JPEG")
        self.pupil.photo.save("x.jpg", ContentFile(buffer.getvalue()))

    def test_small_version_is_made_once_and_kept_long(self):
        self.login(self.sek)
        response = self.client.get(self.pupil.thumb_url)
        self.assertEqual(response["Content-Type"], "image/webp")
        self.assertIn("immutable", response["Cache-Control"])
        image = Image.open(BytesIO(b"".join(response.streaming_content)))
        self.assertLessEqual(max(image.size), 128)
        self.assertEqual(len(list((Path(self.media) / ".thumbs").iterdir())), 1)

    def test_needs_the_same_rights_as_the_photo(self):
        kiga = User.objects.create_user("erz", password="x" * 14, role="kiga")  # Kindergarten staff, or a role nobody knows
        self.login(kiga)
        self.assertEqual(self.client.get(self.pupil.thumb_url).status_code, 403)

    def test_photo_replaced_means_a_new_address(self):
        before = self.pupil.thumb_url
        self.pupil.photo.save("y.jpg", ContentFile(b"x"))
        self.assertNotEqual(self.pupil.thumb_url, before)

    def test_unreadable_photo_falls_back_to_the_original(self):
        self.login(self.sek)
        Path(self.pupil.photo.path).write_bytes(sealed.seal(b"kein bild"))
        self.assertEqual(self.client.get(self.pupil.thumb_url).status_code, 200)
