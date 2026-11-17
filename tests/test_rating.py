"""The field type "Bewertung" (core/fieldtypes.py rating): stars in half steps, for every own table."""
import json
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from django.urls import reverse

from core import fieldtypes as ft
from core.context_user import set_current_user
from core.templatetags.ui import star_share, stars
from tables import formula, infer, services
from tables.models import Field

from .base import AppTestCase


class Spec:
    type = "rating"
    required = False
    display_label = "Bewertung"
    option_values = []

    def __init__(self, **config):
        self.config = config


class CleanTests(SimpleTestCase):
    def test_typed_clicked_and_imported(self):
        spec = Spec()
        self.assertEqual(ft.clean(spec, "3,5"), Decimal("3.5"))
        self.assertEqual(ft.clean(spec, 4), Decimal("4.0"))
        self.assertEqual(ft.clean(spec, "★★★½☆"), Decimal("3.5"))
        self.assertIsNone(ft.clean(spec, "0"))
        self.assertIsNone(ft.clean(spec, ""))
        for wrong in ("5,5", "3,3", "-1", "gut", "NaN"):
            with self.assertRaises(ValidationError):
                ft.clean(spec, wrong)
        with self.assertRaises(ValidationError):
            ft.clean(Spec(half=False), "3,5")
        self.assertEqual(ft.clean(Spec(max=10), "9.5"), Decimal("9.5"))

    def test_stored_and_shown(self):
        spec = Spec()
        self.assertEqual(ft.to_store(spec, ft.clean(spec, "4")), "4.0")
        ft.check_stored(spec, "4.0")
        for wrong in ("4", 4, "6.0", "3.3"):
            with self.assertRaises(ValidationError):
                ft.check_stored(spec, wrong)
        self.assertEqual(ft.display(spec, ft.from_store(spec, "3.5")), "★★★½☆")
        self.assertEqual(ft.display(Spec(max=3), ft.from_store(spec, "3.0")), "★★★")

    def test_stars_are_filled_to_the_middle_of_a_half_star(self):
        self.assertEqual(star_share(0, 5), 0)
        self.assertEqual(star_share(5, 5), 100)
        self.assertEqual(star_share("2.5", 5), round((2 * 26 + 12) / 128 * 100, 2))
        self.assertIn("--pct:", stars("3.5"))
        self.assertIn("3,5 von 5 Sternen", stars("3.5"))

    def test_the_name_suggests_the_type(self):
        self.assertEqual(infer.from_label("Bewertung"), "rating")
        self.assertEqual(infer.from_label("Sterne"), "rating")


class RecordTests(AppTestCase):
    def setUp(self):
        set_current_user(self.it)
        self.table = services.create_table("Filme")
        self.rating = services.create_field(self.table, "Bewertung", "rating", config=services.clean_config("rating", {"max": "5", "half": "1"}))
        self.record = services.create_record(self.table, {"f_name": "Momo", self.rating.key: "4,5"})

    def test_saved_from_the_page_and_the_list(self):
        self.assertEqual(self.record.values[self.rating.key], "4.5")
        self.login(self.it)
        self.client.post(reverse("record_field", args=[self.table.key, self.record.pk, self.rating.key]), {"value": "2.5"})
        self.record.refresh_from_db()
        self.assertEqual(self.record.values[self.rating.key], "2.5")
        response = self.client.post(reverse("table_api_field", args=[self.table.key, self.record.pk, self.rating.key]),
                                    json.dumps({"value": 3}), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.record.refresh_from_db()
        self.assertEqual(self.record.values[self.rating.key], "3.0")
        rows = self.client.get(reverse("table_rows", args=[self.table.key])).json()
        self.assertEqual(rows[0][self.rating.key], 3.0)
        page = self.client.get(reverse("record_detail", args=[self.table.key, self.record.pk]))
        self.assertContains(page, 'role="slider"')
        self.assertContains(page, "ratingInput(3.0, 5, true")

    def test_formulas_count_with_it(self):
        services.create_record(self.table, {"f_name": "Pippi", self.rating.key: "3"})
        avg = services.create_field(self.table, "Doppelt", "formula", config=services.formula_config(self.table, {"expression": "{Bewertung} * 2"}))
        self.assertEqual(avg.config["result"], "number")
        self.assertEqual(formula.display(self.record, avg)[0], "9")

    def test_settings_and_type_changes(self):
        with self.assertRaises(ValidationError):
            services.clean_config("rating", {"max": "12"})
        self.assertEqual(services.clean_config("rating", {"max": "10", "half": "0"}), {"max": 10, "half": False})
        services.change_type(self.rating, "number")
        self.record.refresh_from_db()
        self.assertEqual((self.record.values[self.rating.key], self.rating.config), ("4.5", {"decimals": 1}))
        services.change_type(self.rating, "rating")
        self.record.refresh_from_db()
        self.assertEqual(self.record.values[self.rating.key], "4.5")
        number = services.create_field(self.table, "Punkte", "number")
        services.set_value(self.record, number, "7")
        with self.assertRaises(ValidationError):
            services.change_type(Field.objects.get(pk=number.pk), "rating")  # 7 of 5 stars
