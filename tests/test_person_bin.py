"""Deleting people to the recycle bin, duplicating people and fields: the actions of the right-click menus."""
import json
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from masks.models import FieldDefinition
from masks.views import create_field
from people.models import Card, Person
from vault.models import Credential

from .base import AppTestCase


class PersonBinTests(AppTestCase):
    def bulk(self, action, ids):
        return self.client.post(reverse("bulk_action"), json.dumps({"action": action, "ids": [str(i) for i in ids]}), content_type="application/json")

    def test_delete_hides_the_person_and_brings_back(self):
        self.login(self.sek)
        body = self.bulk("delete", [self.pupil.pk]).json()
        self.assertEqual(body["count"], 1)
        self.assertFalse(Person.objects.filter(pk=self.pupil.pk).exists())
        self.assertEqual(self.client.get(reverse("person_detail", args=[self.pupil.pk])).status_code, 404)
        rows = self.client.get(reverse("api_persons"), {"entity": "pupil", "scope": "all"}).json()
        self.assertNotIn(str(self.pupil.pk), [r["id"] for r in rows])
        self.assertContains(self.client.get(reverse("person_bin")), "Lena Muster")
        self.assertEqual(self.bulk("bring_back", [self.pupil.pk]).json()["count"], 1)
        self.assertTrue(Person.objects.filter(pk=self.pupil.pk).exists())

    def test_restore_from_the_bin_page(self):
        self.login(self.sek)
        self.pupil.recycle()
        self.client.post(reverse("person_bin"), {"person": self.pupil.pk})
        self.assertTrue(Person.objects.filter(pk=self.pupil.pk).exists())

    def test_only_who_may_archive(self):
        self.login(self.sl)
        self.assertEqual(self.bulk("delete", [self.pupil.pk]).status_code, 403)
        self.assertEqual(self.client.get(reverse("person_bin")).status_code, 403)

    def test_gone_for_good_after_keep_days_with_what_belongs_to_the_person(self):
        Card.objects.create(person=self.pupil, code="ABC123")
        Credential.objects.create(person=self.pupil, platform=self.m365)
        self.pupil.recycle()
        Person.everything.filter(pk=self.pupil.pk).update(deleted_at=timezone.now() - timedelta(days=Person.KEEP_DAYS + 1))
        Person.in_the_bin()
        self.assertFalse(Person.everything.filter(pk=self.pupil.pk).exists())
        self.assertFalse(Card.objects.filter(code="ABC123").exists())
        self.assertFalse(Credential.objects.filter(platform=self.m365).exists())


class DuplicateTests(AppTestCase):
    def test_copy_has_data_and_form_fields_but_no_secrets(self):
        secret = create_field("pupil", "Passwort WLAN", "secret")
        plain = create_field("pupil", "Lieblingsfach", "text")
        self.pupil.custom = {plain.key: "Mathe", secret.key: "v1:geheim"}
        self.pupil.birth_place = "Peking"
        self.pupil.save()
        Card.objects.create(person=self.pupil, code="XYZ")
        self.login(self.sek)
        response = self.client.post(reverse("person_duplicate", args=[self.pupil.pk]))
        copy = Person.objects.exclude(pk=self.pupil.pk).get(last_name="Muster")
        self.assertRedirects(response, reverse("person_detail", args=[copy.pk]), fetch_redirect_response=False)
        self.assertEqual(copy.first_name, "Lena (Kopie)")
        self.assertEqual((copy.birth_place, copy.current_class, copy.email), ("Peking", self.group, self.pupil.email))
        self.assertEqual(copy.custom, {plain.key: "Mathe"})
        self.assertFalse(copy.cards.exists())
        self.assertTrue(copy.enrollments.filter(class_group=self.group).exists())

    def test_only_who_may_edit(self):
        self.login(self.sl)
        self.assertEqual(self.client.post(reverse("person_duplicate", args=[self.pupil.pk])).status_code, 403)


class FieldDuplicateTests(AppTestCase):
    def test_builder_gets_the_new_field_as_json(self):
        original = create_field("pupil", "Sportart", "choice", "Fußball\nTennis")
        original.options[1]["color"] = "green"
        original.save()
        self.login(self.it)
        body = self.client.post(reverse("field_duplicate", args=[original.pk]), "{}", content_type="application/json").json()
        copy = FieldDefinition.objects.get(key=body["key"])
        self.assertEqual(copy.label, "Sportart (Kopie)")
        self.assertEqual(copy.options, original.options)
        self.assertEqual(body["id"], str(copy.pk))

    def test_list_of_fields_goes_to_the_new_field(self):
        original = create_field("pupil", "Hobby", "text")
        self.login(self.it)
        response = self.client.post(reverse("field_duplicate", args=[original.pk]))
        copy = FieldDefinition.objects.get(label="Hobby (Kopie)")
        self.assertRedirects(response, reverse("field_edit", args=[copy.pk]))
