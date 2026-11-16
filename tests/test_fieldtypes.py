"""The shared field types (core/fieldtypes.py) and the per-key history of JSON fields (core/models.py)."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone, translation

from core import fieldtypes as ft
from core.context_user import set_current_user
from core.models import ChangeLog
from masks import services as ms
from masks.models import FieldDefinition
from masks.views import USER_TYPES, create_field
from people.blocks import history
from vault.models import Credential

from .base import AppTestCase


def spec(type_, required=False, options=(), **config):
    return SimpleNamespace(type=type_, required=required, display_label="Feld", option_values=list(options),
                           option_label=lambda v: {"a": "Apfel"}.get(v, v), config=config)


class RegistryTests(SimpleTestCase):
    def test_every_person_type_is_registered_and_offered_as_before(self):
        for key, _label in FieldDefinition.TYPES:
            self.assertIn(key, ft.TYPES)
            self.assertTrue(ft.TYPES[key].people, key)
        self.assertEqual([k for k, _ in USER_TYPES], [k for k, _ in FieldDefinition.TYPES if k != "class"])
        self.assertEqual({k for k, _ in ft.choices(for_tables=False)}, {k for k, _ in FieldDefinition.TYPES})

    def test_tables_get_the_new_types_but_not_class(self):
        offered = {k for k, _ in ft.choices()}
        self.assertTrue({"datetime", "link", "file", "image", "formula"} <= offered)
        self.assertNotIn("class", offered)


class CleanAsBeforeTests(SimpleTestCase):
    """What Person's fields always did: the masks helpers now delegate here."""

    def test_person_types(self):
        self.assertIs(ft.clean(spec("bool"), "on"), True)
        self.assertIs(ft.clean(spec("bool"), ""), False)
        self.assertEqual(ft.clean(spec("number"), " 42 "), 42)
        self.assertIsNone(ft.clean(spec("number"), ""))
        self.assertEqual(ft.clean(spec("multichoice", options=["a"]), ""), [])
        self.assertEqual(ft.clean(spec("text"), "  x "), "x")
        self.assertEqual(ft.clean(spec("date"), "24.12.2015"), date(2015, 12, 24))
        self.assertEqual(ft.clean(spec("email"), "A@B.Example"), "a@b.example")
        self.assertEqual(ft.clean(spec("choice", options=["a"]), "a"), "a")
        self.assertEqual(ft.clean(spec("multichoice", options=["a", "b"]), ["a", "b"]), ["a", "b"])
        for kind, raw in (("number", "4,5"), ("email", "x"), ("url", "kein link"), ("phone", "abc"), ("choice", "z"), ("date", "32.1.2020")):
            with self.subTest(kind=kind), self.assertRaises(ValidationError):
                ft.clean(spec(kind, options=["a"]), raw)
        with self.assertRaisesMessage(ValidationError, "Pflichtfeld"):
            ft.clean(spec("text", required=True), " ")

    def test_display_and_form_as_before(self):
        self.assertEqual(ft.display(spec("bool"), True), "Ja")
        self.assertEqual(ft.display(spec("date"), date(2015, 12, 24)), "24.12.2015")
        self.assertEqual(ft.display(spec("number"), 7), 7)
        self.assertEqual(ft.display(spec("secret"), "x"), ft.MASKED)
        self.assertEqual(ft.display(spec("multichoice"), ["a", "b"]), "a, b")
        self.assertEqual(ft.display(spec("choice"), "a", localized=True), "Apfel")
        self.assertEqual(ft.form(spec("date"), date(2015, 12, 24)), "2015-12-24")
        self.assertEqual(ft.form(spec("multichoice"), None), [])
        self.assertEqual(ft.form(spec("secret"), "x"), ft.MASKED)


class NewTypeTests(SimpleTestCase):
    def test_numbers_with_decimals_unit_and_limits(self):
        price = spec("number", decimals=2, unit="€", min=0, max=1000)
        self.assertEqual(ft.clean(price, "34,5"), Decimal("34.50"))
        self.assertEqual(ft.clean(price, "12.5"), Decimal("12.50"))
        self.assertEqual(ft.clean(price, "999,999"), Decimal("1000.00"))
        with self.assertRaisesMessage(ValidationError, "mindestens"):
            ft.clean(price, "-1")
        with self.assertRaisesMessage(ValidationError, "höchstens"):
            ft.clean(price, "1000,01")
        for bad in ("abc", "NaN", "Infinity"):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                ft.clean(price, bad)
        with translation.override("de"):
            self.assertEqual(ft.display(price, Decimal("1234.5"), localized=True), "1.234,50 €")
        self.assertEqual(ft.display(price, Decimal("1234.5")), "1234.50 €")
        self.assertEqual(ft.to_store(price, Decimal("12.50")), "12.50")
        self.assertEqual(ft.from_store(price, "12.50"), Decimal("12.50"))
        self.assertEqual(ft.form(price, Decimal("12.50")), "12.50")

    @override_settings(TIME_ZONE="Europe/Berlin")
    def test_datetime_in_the_database_zone(self):
        moment = ft.clean(spec("datetime"), "24.12.2026 18:30")
        self.assertTrue(timezone.is_aware(moment))
        self.assertEqual(ft.display(spec("datetime"), moment), "24.12.2026 18:30")
        self.assertEqual(ft.form(spec("datetime"), moment), "2026-12-24T18:30")
        self.assertEqual(ft.clean(spec("datetime"), "2026-12-24T18:30"), moment)
        stored = ft.to_store(spec("datetime"), moment)
        self.assertEqual(ft.from_store(spec("datetime"), stored), moment)
        with self.assertRaises(ValidationError):
            ft.clean(spec("datetime"), "morgen")

    def test_links_are_ids_and_single_unless_multiple(self):
        a, b = "6f1c1e3a-1b2c-4d5e-8f90-0123456789ab", "7f1c1e3a-1b2c-4d5e-8f90-0123456789ab"
        self.assertEqual(ft.clean(spec("link"), a), [a])
        self.assertEqual(ft.clean(spec("link"), ""), [])
        self.assertEqual(ft.clean(spec("link", multiple=True), [a, b, a]), [a, b])
        with self.assertRaisesMessage(ValidationError, "nur ein"):
            ft.clean(spec("link"), [a, b])
        with self.assertRaisesMessage(ValidationError, "Unbekannter Datensatz"):
            ft.clean(spec("link"), "../etc")

    def test_computed_and_files_are_not_typed_in(self):
        with self.assertRaisesMessage(ValidationError, "berechnet"):
            ft.clean(spec("formula"), "1")
        with self.assertRaisesMessage(ValidationError, "hochgeladen"):
            ft.clean(spec("image"), "x.png")

    def test_values_that_no_longer_fit_come_back_unchanged(self):
        self.assertEqual(ft.from_store(spec("date"), "nächste Woche"), "nächste Woche")
        self.assertEqual(ft.from_store(spec("number"), "viele"), "viele")
        self.assertEqual(ft.display(spec("date"), ft.from_store(spec("date"), "nächste Woche")), "nächste Woche")
        self.assertEqual(ft.from_store(spec("date"), "2026-01-02"), date(2026, 1, 2))
        self.assertIsNone(ft.from_store(spec("number"), ""))


class HistoryPerKeyTests(AppTestCase):
    def setUp(self):
        set_current_user(self.it)

    def test_custom_fields_are_logged_by_key(self):
        hobby = create_field("pupil", "Hobby", "text")
        ms.set_value(self.pupil, hobby, "Schach")
        ms.set_value(self.pupil, hobby, "Go")
        rows = ChangeLog.objects.filter(record_id=self.pupil.pk, field=f"custom.{hobby.key}").order_by("at", "hlc")
        self.assertEqual([(r.old_value, r.new_value) for r in rows], [("", "Schach"), ("Schach", "Go")])
        self.assertFalse(ChangeLog.objects.filter(record_id=self.pupil.pk, field="custom").exists())

    def test_secret_custom_fields_are_named_but_never_shown(self):
        pin = create_field("pupil", "PIN", "secret")
        ms.set_value(self.pupil, pin, "1234-geheim")
        row = ChangeLog.objects.get(record_id=self.pupil.pk, field=f"custom.{pin.key}")
        self.assertEqual(row.new_value, "[verschlüsselt]")
        self.assertNotIn("1234-geheim", " ".join(ChangeLog.objects.values_list("new_value", flat=True)))

    def test_history_block_names_custom_fields_like_the_form(self):
        hobby = create_field("pupil", "Hobby", "text")
        ms.set_value(self.pupil, hobby, "Schach")
        labels = [c["label"] for c in history(None, self.pupil)["changes"]]
        self.assertIn("Hobby", labels)
        self.login(self.it)
        self.assertEqual(self.client.get(reverse("person_detail", args=[self.pupil.pk])).status_code, 200)

    def test_secret_json_fields_stay_one_masked_row(self):
        credential = Credential.objects.create(person=self.pupil, platform=self.m365, values={"username": "lena"}, secrets={"password": "x"})
        rows = ChangeLog.objects.filter(record_id=credential.pk, field__startswith="secrets")
        self.assertEqual([r.field for r in rows], ["secrets"])
        self.assertEqual(rows[0].new_value, "[verschlüsselt]")

    def test_new_record_logs_only_filled_keys(self):
        hobby = create_field("pupil", "Hobby", "text")
        person = type(self.pupil).objects.create(kind="pupil", first_name="Ada", last_name="Neu", custom={hobby.key: "Lesen", "x_leer": ""})
        fields = set(ChangeLog.objects.filter(record_id=person.pk).values_list("field", flat=True))
        self.assertIn(f"custom.{hobby.key}", fields)
        self.assertNotIn("custom.x_leer", fields)
