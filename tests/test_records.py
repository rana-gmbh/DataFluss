import json
from datetime import date

from django.core.exceptions import ValidationError
from django.urls import reverse

from core.models import ChangeLog
from devices.models import Asset, AssetCategory, Assignment, ReturnCampaign, SupportCase
from masks import services as ms
from masks.models import FieldDefinition, Mask, MaskVersion
from masks.views import create_field
from people.models import Card, ClassGroup, Enrollment, Guardian, Guardianship, Person, SchoolYear
from people.services import next_class_name, parse_card_input, rollover_commit

from .base import AppTestCase


class FieldTests(AppTestCase):
    def fd(self, key):
        return FieldDefinition.objects.get(entity="pupil", key=key)

    def test_validation_messages(self):
        with self.assertRaises(ValidationError):
            ms.set_value(self.pupil, self.fd("first_name"), "")
        with self.assertRaises(ValidationError):
            ms.set_value(self.pupil, self.fd("email"), "keine-mail")
        with self.assertRaises(ValidationError):
            ms.set_value(self.pupil, self.fd("school_type"), "Quatsch")
        with self.assertRaises(ValidationError):
            ms.set_value(self.pupil, self.fd("birth_date"), "32.13.2020")

    def test_dates_and_email_are_normalised(self):
        ms.set_value(self.pupil, self.fd("birth_date"), "24.12.2015")
        ms.set_value(self.pupil, self.fd("email"), "Lena@Schule.Example")
        person = Person.objects.get(pk=self.pupil.pk)
        self.assertEqual(person.birth_date, date(2015, 12, 24))
        self.assertEqual(person.email, "lena@schule.example")

    def test_change_log_records_old_and_new(self):
        from core.context_user import set_current_user

        set_current_user(self.it)
        ms.set_value(self.pupil, self.fd("birth_place"), "Peking")
        entry = ChangeLog.objects.get(record_id=self.pupil.pk, field="birth_place")
        self.assertEqual(entry.new_value, "Peking")
        self.assertEqual(entry.user, self.it)
        self.assertRegex(entry.hlc, r"^\d{15}-\d{5}-")

    def test_autosave_endpoint_and_undo_payload(self):
        self.login(self.sek)
        response = self.client.post(reverse("person_field", args=[self.pupil.pk, "birth_place"]), {"value": "Wien"})
        self.assertEqual(response.status_code, 204)
        trigger = json.loads(response.headers["HX-Trigger"])["fieldSaved"]
        self.assertEqual(trigger["old"], "")
        self.assertTrue(trigger["undoable"])

    def test_error_comes_back_as_message(self):
        self.login(self.sek)
        response = self.client.post(reverse("person_field", args=[self.pupil.pk, "email"]), {"value": "x"})
        self.assertIn("fieldError", response.headers["HX-Trigger"])

    def test_changing_class_keeps_history(self):
        fd = self.fd("klasse")
        ms.set_value(self.pupil, fd, str(self.group.pk))
        other = ClassGroup.objects.create(name="6a", level=6, school_year=self.year)
        self.pupil.current_class = None
        self.pupil.save()
        ms.set_value(self.pupil, fd, str(self.group.pk))
        ms.set_value(self.pupil, fd, str(other.pk))
        self.assertEqual(Person.objects.get(pk=self.pupil.pk).current_class, other)
        self.assertEqual(Enrollment.objects.filter(person=self.pupil).count(), 2)
        self.assertEqual(Enrollment.objects.filter(person=self.pupil, end__isnull=True).count(), 1)

    def test_archiving_sets_exit_date(self):
        ms.set_value(self.pupil, self.fd("archived"), "1")
        self.assertIsNotNone(Person.objects.get(pk=self.pupil.pk).exit_date)

    def test_family_name_first(self):
        person = Person(first_name="小明", last_name="王", family_name_first=True)
        self.assertEqual(person.display_name, "王小明")


class CustomFieldTests(AppTestCase):
    def test_add_set_and_show_without_code_change(self):
        fd = create_field("pupil", "Allergien", "text", in_grid=True)
        ms.set_value(self.pupil, fd, "Nüsse")
        self.assertEqual(Person.objects.get(pk=self.pupil.pk).custom[fd.key], "Nüsse")
        self.login(self.it)
        rows = self.client.get("/api/personen/?entity=pupil").json()
        self.assertEqual(rows[0][fd.key], "Nüsse")

    def test_choice_needs_options(self):
        with self.assertRaises(ValidationError):
            create_field("pupil", "Farbe", "choice", "")

    def test_duplicate_labels_get_unique_keys(self):
        self.assertNotEqual(create_field("pupil", "Test", "text").key, create_field("pupil", "Test", "text").key)

    def test_archiving_keeps_data(self):
        fd = create_field("pupil", "Alt", "text")
        ms.set_value(self.pupil, fd, "bleibt")
        self.login(self.it)
        self.client.post(reverse("field_edit", args=[fd.pk]), {"action": "archive"})
        self.assertTrue(FieldDefinition.objects.get(pk=fd.pk).archived)
        self.assertEqual(Person.objects.get(pk=self.pupil.pk).custom[fd.key], "bleibt")

    def test_core_field_cannot_be_archived(self):
        fd = FieldDefinition.objects.get(entity="pupil", key="email")
        self.login(self.it)
        self.client.post(reverse("field_edit", args=[fd.pk]), {"action": "archive", "label": fd.label})
        self.assertFalse(FieldDefinition.objects.get(pk=fd.pk).archived)


class MaskTests(AppTestCase):
    def mask(self):
        return Mask.objects.get(entity="pupil", is_default=True)

    def tree(self):
        return {"tabs": [{"id": "t", "title": "Neu", "sections": [{"id": "s", "title": "A", "items": [
            {"type": "field", "field": "first_name", "w": 6},
            {"type": "field", "field": "bus", "w": 3, "show_if": {"field": "school_type", "op": "eq", "value": "GS"}},
            {"type": "block", "block": "logins", "w": 12},
            {"type": "bad", "w": 12},
        ]}]}]}

    def test_draft_does_not_change_live_mask_until_published(self):
        self.login(self.it)
        mask = self.mask()
        before = mask.published.number
        self.client.post(reverse("mask_save", args=[mask.pk]), json.dumps({"tree": self.tree()}), content_type="application/json")
        self.assertEqual(self.mask().published.number, before)
        self.assertIsNotNone(self.mask().draft)
        response = self.client.post(reverse("mask_save", args=[mask.pk]), json.dumps({"tree": self.tree(), "publish": True}), content_type="application/json")
        self.assertEqual(response.json()["published"], before + 1)
        self.assertIsNone(self.mask().draft)
        self.assertEqual(self.mask().published.tree["tabs"][0]["title"], "Neu")

    def test_unknown_items_are_dropped(self):
        self.login(self.it)
        self.client.post(reverse("mask_save", args=[self.mask().pk]), json.dumps({"tree": self.tree(), "publish": True}), content_type="application/json")
        items = self.mask().published.tree["tabs"][0]["sections"][0]["items"]
        self.assertEqual(len(items), 3)

    def test_condition_hides_field(self):
        tabs = ms.build_view(self.tree(), self.pupil, self.it)
        keys = [i["fd"].key for i in tabs[0]["sections"][0]["items"] if i["type"] == "field"]
        self.assertEqual(keys, ["first_name"])
        self.pupil.school_type = "GS"
        tabs = ms.build_view(self.tree(), self.pupil, self.it)
        self.assertIn("bus", [i["fd"].key for i in tabs[0]["sections"][0]["items"] if i["type"] == "field"])

    def test_mask_does_not_grant_access(self):
        tree = self.tree()
        tabs = ms.build_view(tree, self.pupil, self.sl)
        blocks = [i for i in tabs[0]["sections"][0]["items"] if i["type"] == "block"]
        self.assertEqual(blocks, [])

    def test_read_only_user_gets_no_editable_fields(self):
        tabs = ms.build_view(self.tree(), self.pupil, self.sl)
        self.assertFalse(tabs[0]["sections"][0]["items"][0]["editable"])

    def test_specific_mask_wins_and_rollback_works(self):
        guest = Mask.objects.create(entity="pupil", name="Gast", match_field="school_type", match_value="Gast")
        MaskVersion.objects.create(mask=guest, number=1, status="published", tree={"tabs": [{"id": "g", "title": "Gast", "sections": []}]})
        self.pupil.school_type = "Gast"
        self.assertEqual(ms.resolve_mask(self.pupil, self.it)[0], guest)
        self.pupil.school_type = "GY"
        self.assertEqual(ms.resolve_mask(self.pupil, self.it)[0], self.mask())
        self.login(self.it)
        mask = self.mask()
        original = mask.published
        self.client.post(reverse("mask_save", args=[mask.pk]), json.dumps({"tree": self.tree(), "publish": True}), content_type="application/json")
        self.client.post(reverse("mask_action", args=[mask.pk]), {"action": "restore", "number": original.number})
        self.assertEqual(self.mask().published.tree, original.tree)


class ClassTests(AppTestCase):
    def test_next_class_name(self):
        self.assertEqual(next_class_name(ClassGroup(name="5a", level=5, kind="klasse")), "6a")
        self.assertEqual(next_class_name(ClassGroup(name="9b", level=9, kind="klasse")), "10b")
        self.assertIsNone(next_class_name(ClassGroup(name="Rote Gruppe", kind="gruppe")))

    def test_rollover_moves_pupils_and_retires_leavers(self):
        leaver = Person.objects.create(kind="pupil", first_name="A", last_name="B", current_class=self.top, grade_level=12)
        moved, left = rollover_commit("2027/28", date(2027, 8, 1), date(2028, 7, 31))
        self.assertEqual((moved, left), (1, 1))
        self.assertEqual(Person.objects.get(pk=self.pupil.pk).current_class.name, "6a")
        self.assertTrue(Person.objects.get(pk=leaver.pk).archived)
        self.assertEqual(SchoolYear.current().name, "2027/28")

    def test_card_input_hex_to_decimal(self):
        self.assertEqual(parse_card_input("DEADBEEF", "hex"), "3735928559")
        self.assertEqual(parse_card_input("xyz", "hex"), "")
        self.assertEqual(parse_card_input("12345", "dec"), "12345")

    def test_card_assign_lookup_and_duplicate(self):
        self.login(self.sek)
        self.client.post(reverse("person_card", args=[self.pupil.pk]), {"raw": "ff"})
        self.assertEqual(Card.objects.get(person=self.pupil).code, "255")
        response = self.client.post(reverse("card_lookup"), {"raw": "FF"})
        self.assertEqual(response.headers["Location"], f"/personen/{self.pupil.pk}/")
        other = Person.objects.create(kind="pupil", first_name="X", last_name="Y")
        response = self.client.post(reverse("person_card", args=[other.pk]), {"raw": "ff"})
        self.assertIn("gehört bereits", json.loads(response.headers["HX-Trigger"])["toast"])
        self.assertFalse(Card.objects.filter(person=other).exists())

    def test_guardian_shared_across_siblings(self):
        self.login(self.sek)
        sibling = Person.objects.create(kind="pupil", first_name="Tom", last_name="Muster")
        for person in (self.pupil, sibling):
            self.client.post(reverse("person_guardian", args=[person.pk]), {"action": "add", "first_name": "Eva", "last_name": "Muster", "email": "eva@eltern.example"})
        self.assertEqual(Guardian.objects.count(), 1)
        self.assertEqual(Guardianship.objects.count(), 2)


class DeviceTests(AppTestCase):
    def setUp(self):
        self.asset = Asset.objects.create(number="SuS1", serial="005190920853")

    def test_cannot_issue_twice(self):
        self.asset.issue(person=self.pupil)
        with self.assertRaises(ValueError):
            self.asset.issue(person=self.pupil)
        self.assertEqual(self.asset.status, "Ausgegeben")

    def test_return_keeps_history(self):
        self.asset.issue(person=self.pupil, when=date(2026, 1, 1))
        self.asset.take_back(date(2026, 2, 1))
        self.asset.issue(person=self.pupil, when=date(2026, 3, 1))
        self.assertEqual(self.asset.assignments.count(), 2)

    def test_selling_closes_assignment(self):
        self.asset.issue(person=self.pupil)
        self.asset.sell(date(2026, 7, 1))
        self.assertEqual(Asset.objects.get(pk=self.asset.pk).status, "Verkauft")
        self.assertFalse(Assignment.objects.filter(asset=self.asset, returned_at__isnull=True).exists())

    def test_device_numbers_never_collide_between_servers(self):
        category = AssetCategory.objects.create(name="Test", prefix="T", digits=3, next_number=1)
        with self.settings(DEVICE_NUMBER_STEP=2, DEVICE_NUMBER_OFFSET=0):
            school = [category.allocate_number() for _ in range(3)]
        with self.settings(DEVICE_NUMBER_STEP=2, DEVICE_NUMBER_OFFSET=1):
            category.refresh_from_db()
            category.next_number = 1
            category.save()
            eu = [category.allocate_number() for _ in range(3)]
        self.assertFalse(set(school) & set(eu))

    def test_damage_case_flags_device_and_closing_clears_it(self):
        self.login(self.it)
        self.asset.issue(person=self.pupil)
        self.client.post(reverse("case_new"), {"person": self.pupil.pk, "description": "Display", "parts": ["Display"], "status": "Offen"})
        case = SupportCase.objects.get()
        self.assertEqual(case.asset, self.asset)
        self.assertTrue(Asset.objects.get(pk=self.asset.pk).damaged)
        self.client.post(reverse("case_detail", args=[case.pk]), {"description": "Display", "status": "Erledigt"})
        self.assertFalse(Asset.objects.get(pk=self.asset.pk).damaged)

    def test_damage_pictures_open_large_on_click(self):
        from django.core.files.base import ContentFile
        from people.models import Attachment
        case = SupportCase.objects.create(person=self.pupil, description="Display")
        picture = Attachment.objects.create(support_case=case, file=ContentFile(b"x", name="riss.jpg"), name="riss.jpg")
        self.login(self.it)
        self.assertContains(self.client.get(reverse("case_detail", args=[case.pk])), f'data-zoom="{picture.file.url}"')

    def test_return_campaign_collects_open_devices(self):
        self.asset.issue(person=self.pupil)
        self.login(self.it)
        self.client.post(reverse("campaigns"), {"name": "Ende", "date": "2027-06-18", "level": "5"})
        campaign = ReturnCampaign.objects.get()
        item = campaign.items.get()
        self.client.post(reverse("campaign_detail", args=[campaign.pk]), {"item": item.pk})
        self.assertEqual(Asset.objects.get(pk=self.asset.pk).status, "Verfügbar")


class PanelTests(AppTestCase):
    def test_panel_shows_record_with_mask_tabs(self):
        self.login(self.sek)
        response = self.client.get(reverse("person_panel", args=[self.pupil.pk]))
        self.assertContains(response, "Lena Muster")
        self.assertContains(response, "IT Informationen")
        self.assertContains(response, 'hx-trigger="intersect once"')

    def test_photo_can_be_enlarged_in_the_record(self):
        self.login(self.sek)
        panel = reverse("person_panel", args=[self.pupil.pk])
        self.assertNotContains(self.client.get(panel), "data-zoom")
        Person.objects.filter(pk=self.pupil.pk).update(photo="personen/lena.jpg")
        self.assertContains(self.client.get(panel), 'data-zoom="/media/personen/lena.jpg"')
        block = reverse("person_block", args=[self.pupil.pk, "photo"])
        self.assertContains(self.client.get(block), 'data-zoom="/media/personen/lena.jpg"')

    def test_panel_requires_sign_in(self):
        self.assertEqual(self.client.get(reverse("person_panel", args=[self.pupil.pk])).status_code, 302)

    def test_panel_respects_roles(self):
        self.login(self.sl)
        response = self.client.get(reverse("person_panel", args=[self.pupil.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "/block/logins/")
        self.assertNotContains(response, 'hx-post="')

    def test_read_only_users_see_dates_and_no_empty_tabs(self):
        Person.objects.filter(pk=self.pupil.pk).update(birth_date=date(2013, 7, 6))
        self.login(self.sl)
        response = self.client.get(reverse("person_panel", args=[self.pupil.pk]))
        self.assertContains(response, "06.07.2013")
        self.assertNotContains(response, "eBooks &amp; Passwörter")

    def test_single_row_for_list_refresh(self):
        self.login(self.it)
        rows = self.client.get("/api/personen/", {"entity": "pupil", "id": str(self.pupil.pk)}).json()
        self.assertEqual([r["first_name"] for r in rows], ["Lena"])
        self.assertEqual(rows[0]["klasse"], "5a")
        self.assertEqual(self.client.get("/api/personen/", {"entity": "pupil", "id": "kaputt"}).status_code, 400)

    def test_single_row_stays_inside_the_requested_area(self):
        self.login(self.it)
        rows = self.client.get("/api/personen/", {"entity": "staff", "id": str(self.pupil.pk)}).json()
        self.assertEqual(rows, [])

    def test_saved_view_keeps_column_filters(self):
        self.login(self.sl)
        payload = {"entity": "pupil", "name": "Mittelstufe", "config": {"filters": {"klasse": ["7a", "7b"]}}}
        response = self.client.post("/api/ansichten/", json.dumps(payload), content_type="application/json")
        self.assertEqual(response.json()["config"]["filters"]["klasse"], ["7a", "7b"])


class RecordPanelTests(AppTestCase):
    def setUp(self):
        self.guardian = Guardian.objects.create(first_name="Eva", last_name="Muster", email="eva@example.org")
        Guardianship.objects.create(person=self.pupil, guardian=self.guardian, role="Mutter")
        self.asset = Asset.objects.create(number="SuS-0042", serial="SN42")
        self.case = SupportCase.objects.create(person=self.pupil, asset=self.asset, description="Display gesprungen")
        self.panels = {
            reverse("guardian_panel", args=[self.guardian.pk]): "Eva Muster",
            reverse("asset_panel", args=[self.asset.pk]): "SuS-0042",
            reverse("case_panel", args=[self.case.pk]): "Display gesprungen",
        }

    def test_panels_show_the_record_without_the_page_around_it(self):
        self.login(self.sek)
        for url, text in self.panels.items():
            response = self.client.get(url)
            self.assertContains(response, text)
            self.assertContains(response, "data-panel-close")
            self.assertNotContains(response, "<html")

    def test_panels_require_sign_in(self):
        for url in self.panels:
            self.assertEqual(self.client.get(url).status_code, 302)

    def test_lists_open_their_records_in_the_panel(self):
        self.login(self.sek)
        self.assertContains(self.client.get(reverse("guardians")), f'data-record="{self.guardian.pk}"')
        self.assertContains(self.client.get(reverse("cases")), f'data-record="{self.case.pk}"')
        for name in ("guardians", "cases", "assets", "pupils", "staff"):
            response = self.client.get(reverse(name))
            self.assertContains(response, 'id="record-panel"')
            self.assertContains(response, 'id="record-panel-divider"')

    def test_panel_forms_name_their_own_address(self):
        self.login(self.it)
        for url, target in (
            (reverse("guardian_panel", args=[self.guardian.pk]), reverse("guardian_detail", args=[self.guardian.pk])),
            (reverse("asset_panel", args=[self.asset.pk]), reverse("asset_action", args=[self.asset.pk])),
            (reverse("case_panel", args=[self.case.pk]), reverse("case_detail", args=[self.case.pk])),
        ):
            self.assertContains(self.client.get(url), f'action="{target}"')

    def test_saved_message_arrives_with_the_reloaded_panel(self):
        self.login(self.it)
        self.client.post(reverse("guardian_detail", args=[self.guardian.pk]), {"first_name": "Eva", "last_name": "Neu"})
        response = self.client.get(reverse("guardian_panel", args=[self.guardian.pk]))
        self.assertContains(response, "Eva Neu")
        self.assertContains(response, 'data-toast="Gespeichert."')

    def test_read_only_users_get_no_save_button(self):
        self.login(self.sl)
        for url in self.panels:
            self.assertNotContains(self.client.get(url), "Speichern")

    def test_case_panel_has_no_back_link(self):
        self.login(self.it)
        self.assertNotContains(self.client.get(reverse("case_panel", args=[self.case.pk])), "Zurück")
        self.assertContains(self.client.get(reverse("case_detail", args=[self.case.pk])), "Zurück")

    def test_single_device_row_for_list_refresh(self):
        self.login(self.it)
        rows = self.client.get("/api/geraete/", {"id": str(self.asset.pk)}).json()
        self.assertEqual([r["number"] for r in rows], ["SuS-0042"])
        self.assertEqual(self.client.get("/api/geraete/", {"id": "kaputt"}).status_code, 400)
