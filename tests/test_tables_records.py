"""Own tables, step 3 (docs/TABLES-PLAN.md): lists, record pages, masks of tables, views, sidebar and search."""
import json

from django.urls import reverse

from core.context_user import set_current_user
from core.models import Role, SavedView
from masks.models import Mask, MaskVersion
from sync.scope import Scope
from tables import masks as table_masks, services
from tables.models import EDIT, READ, Record

from .test_tables import TableCase


class RecordCase(TableCase):
    def setUp(self):
        super().setUp()
        services.set_rights(self.table, {Role.SEKRETARIAT: EDIT, Role.SCHULLEITUNG: READ})
        self.momo = services.create_record(self.table, {"f_name": "Momo", "f_preis": "12,5", "f_genre": "Roman"})
        set_current_user(None)

    def url(self, name, *args):
        return reverse(name, args=["buecher", *args])


class ListTests(RecordCase):
    def test_list_page_and_rows_follow_the_rights(self):
        self.login(self.sl)
        page = self.client.get(self.url("table_records"))
        self.assertEqual(page.status_code, 200)
        config = json.loads(page.context["config"] and json.dumps(page.context["config"]))
        self.assertTrue(config["records"])
        self.assertFalse(config["canEdit"])
        self.assertFalse(any(c["editable"] for c in config["columns"]))
        self.assertEqual({c["key"]: c.get("decimals") for c in config["columns"]}["f_preis"], 2)
        rows = self.client.get(self.url("table_rows")).json()
        self.assertEqual(rows, [{"id": str(self.momo.pk), "name": "Momo", "f_name": "Momo", "f_preis": 12.5, "f_genre": "Roman", "f_isbn": None}])
        self.assertEqual(len(self.client.get(self.url("table_rows"), {"id": str(self.momo.pk)}).json()), 1)

    def test_no_right_no_page(self):
        services.set_rights(self.table, {Role.SCHULLEITUNG: "none"})
        self.login(self.sl)
        self.assertEqual(self.client.get(self.url("table_records")).status_code, 403)
        self.assertEqual(self.client.get(self.url("table_rows")).status_code, 403)
        self.assertEqual(self.client.get(self.url("record_detail", self.momo.pk)).status_code, 403)
        self.assertEqual(self.client.get(reverse("table_records", args=["gibt-es-nicht"])).status_code, 403)  # no hint which tables exist
        self.login(self.it)
        self.assertEqual(self.client.get(reverse("table_records", args=["gibt-es-nicht"])).status_code, 404)

    def test_inline_edit_needs_the_edit_right_and_a_valid_value(self):
        field_url = self.url("table_api_field", self.momo.pk, "f_preis")
        self.login(self.sl)
        self.assertEqual(self.client.post(field_url, json.dumps({"value": 3}), content_type="application/json").status_code, 403)
        self.login(self.sek)
        self.assertEqual(self.client.post(field_url, json.dumps({"value": 3}), content_type="application/json").status_code, 200)
        self.assertEqual(Record.objects.get(pk=self.momo.pk).values["f_preis"], "3.00")
        bad = self.client.post(self.url("table_api_field", self.momo.pk, "f_genre"), json.dumps({"value": "Krimi"}), content_type="application/json")
        self.assertEqual(bad.status_code, 422)
        self.assertIn("Auswahl", bad.json()["error"])

    def test_new_record_bin_and_bring_back(self):
        self.login(self.sek)
        answer = self.client.post(self.url("record_new"))
        record = Record.objects.exclude(pk=self.momo.pk).get(table=self.table)
        self.assertRedirects(answer, self.url("record_detail", record.pk))
        result = self.client.post(self.url("records_action"), json.dumps({"ids": [str(record.pk)], "action": "delete"}), content_type="application/json").json()
        self.assertEqual(result, {"count": 1, "refused": []})
        self.assertFalse(Record.objects.filter(pk=record.pk).exists())
        self.assertContains(self.client.get(self.url("records_bin")), "(ohne Titel)")
        self.client.post(self.url("records_bin"), {"record": str(record.pk)})
        self.assertTrue(Record.objects.filter(pk=record.pk).exists())

    def test_saved_views_belong_to_the_table(self):
        self.login(self.sl)
        answer = self.client.post(self.url("table_views"), json.dumps({"name": "Romane", "config": {"filters": {"f_genre": ["Roman"]}}}), content_type="application/json")
        view = SavedView.objects.get(pk=answer.json()["id"])
        self.assertEqual(view.entity, "table:buecher")
        self.assertEqual(json.loads(json.dumps(self.client.get(self.url("table_records")).context["config"]))["views"][0]["name"], "Romane")
        self.client.post(self.url("table_view_delete", view.pk))
        self.assertFalse(SavedView.objects.filter(pk=view.pk).exists())


class RecordPageTests(RecordCase):
    def test_page_panel_and_form_without_a_mask_show_every_field(self):
        self.login(self.sl)
        for name in ("record_detail", "record_panel", "record_form"):
            page = self.client.get(self.url(name, self.momo.pk))
            self.assertContains(page, "Preis")
            self.assertContains(page, "Genre")
        self.assertNotContains(self.client.get(self.url("record_detail", self.momo.pk)), 'hx-post="/tabellen/')  # read only

    def test_field_saved_on_change_like_the_person_page(self):
        self.login(self.sek)
        answer = self.client.post(self.url("record_field", self.momo.pk, "f_name"), {"value": "Momo (neu)"})
        trigger = json.loads(answer["HX-Trigger"])["fieldSaved"]
        self.assertEqual((trigger["old"], trigger["reload"]), ("Momo", True))
        self.assertEqual(Record.objects.get(pk=self.momo.pk).title, "Momo (neu)")
        answer = self.client.post(self.url("record_field", self.momo.pk, "f_preis"), {"value": "billig"})
        self.assertIn("fieldError", json.loads(answer["HX-Trigger"]))

    def test_secrets_are_revealed_only_with_the_vault_right(self):
        code = services.create_field(self.table, "Code", "secret")
        services.set_value(self.momo, code, "geheim-4711")
        self.login(self.sl)
        self.assertEqual(self.client.post(self.url("record_secret_reveal", self.momo.pk, "f_code")).status_code, 403)
        self.assertNotContains(self.client.get(self.url("record_detail", self.momo.pk)), "f-f_code")
        self.login(self.it)
        self.assertContains(self.client.post(self.url("record_secret_reveal", self.momo.pk, "f_code")), "geheim-4711")

    def test_history_block_names_the_fields(self):
        set_current_user(self.it)
        services.set_value(self.momo, self.price, "13")
        self.login(self.sl)
        block = self.client.get(self.url("record_block", self.momo.pk, "history"))
        self.assertContains(block, "Preis")
        self.assertEqual(self.client.get(self.url("record_block", self.momo.pk, "erfunden")).status_code, 404)


class TableMaskTests(RecordCase):
    def test_designer_works_on_the_tables_fields_and_blocks(self):
        self.login(self.it)
        answer = self.client.post(self.url("table_mask"))
        mask = Mask.objects.get(table=self.table)
        self.assertRedirects(answer, reverse("mask_builder", args=[mask.pk]))
        self.assertEqual(self.client.post(self.url("table_mask")).status_code, 302)
        self.assertEqual(Mask.objects.filter(table=self.table).count(), 1)
        page = self.client.get(reverse("mask_builder", args=[mask.pk]))
        config = page.context["config"]
        self.assertEqual([f["key"] for f in config["fields"]][:2], ["f_name", "f_preis"])
        self.assertEqual([b["key"] for b in config["blocks"]], ["history"])
        self.assertIn("/einstellungen/tabellen/buecher/felder/neu/", config["fieldCreateUrl"])
        self.assertNotIn(mask, list(self.client.get(reverse("mask_list")).context["masks"]))

    def test_saving_keeps_only_the_tables_blocks_and_unplaced_fields_still_show(self):
        self.login(self.it)
        mask = table_masks.ensure_mask(self.table, self.it)
        tree = {"tabs": [{"id": "t1", "title": "Buch", "sections": [{"id": "s1", "title": "", "items": [
            {"type": "field", "field": "f_name", "w": 12},
            {"type": "field", "field": "f_preis", "w": 6, "show_if": {"field": "f_genre", "op": "eq", "value": "Sachbuch"}},
            {"type": "block", "block": "history", "w": 12}, {"type": "block", "block": "logins", "w": 12}]}]}]}
        self.client.post(reverse("mask_save", args=[mask.pk]), json.dumps({"tree": tree, "publish": True}), content_type="application/json")
        published = Mask.objects.get(pk=mask.pk).published.tree
        self.assertEqual([i.get("block") for i in published["tabs"][0]["sections"][0]["items"] if i["type"] == "block"], ["history"])
        tabs = table_masks.build_view(published, self.momo, self.it)
        shown = [i["fd"].key for t in tabs for s in t["sections"] for i in s["items"] if i["type"] == "field"]
        self.assertNotIn("f_preis", shown)  # hidden by its condition, not moved to "Weitere Felder"
        self.assertIn("f_genre", shown)
        self.assertEqual(tabs[-1]["sections"][-1]["title"], "Weitere Felder")
        preview = self.client.post(reverse("mask_preview", args=[mask.pk]), json.dumps({"tree": published}), content_type="application/json")
        self.assertContains(preview, "Momo")

    def test_new_field_from_the_designer(self):
        self.login(self.it)
        answer = self.client.post(self.url("table_field_new"), json.dumps({"label": "Verlag", "type": "text"}), content_type="application/json")
        self.assertEqual(answer.json()["key"], "f_verlag")

    def test_deleting_a_table_takes_its_masks(self):
        empty = services.create_table("Leer")
        mask = table_masks.ensure_mask(empty, self.it)
        services.delete_table(empty)
        self.assertFalse(Mask.objects.filter(pk=mask.pk).exists())
        self.assertFalse(MaskVersion.objects.filter(mask_id=mask.pk).exists())


class ReachTests(RecordCase):
    def test_masks_travel_only_with_readable_tables(self):
        hidden = services.create_table("Intern")
        hidden_mask = table_masks.ensure_mask(hidden, self.it)
        mask = table_masks.ensure_mask(self.table, self.it)
        held = Scope(self.sl).records()
        self.assertIn(mask.pk, held[Mask])
        self.assertNotIn(hidden_mask.pk, held[Mask])
        self.assertNotIn(hidden.pk, held.get(type(hidden), set()))
        self.assertIn(hidden_mask.pk, Scope(self.it).records()[Mask])

    def test_sidebar_and_search(self):
        self.login(self.sl)
        dashboard = self.client.get(reverse("dashboard"))
        self.assertContains(dashboard, 'href="/tabellen/buecher/"')
        found = self.client.get(reverse("global_search"), {"q": "momo"})
        self.assertContains(found, "/tabellen/buecher/")
        services.set_rights(self.table, {Role.SCHULLEITUNG: "none"})
        self.assertNotContains(self.client.get(reverse("dashboard")), 'href="/tabellen/buecher/"')
        self.assertNotContains(self.client.get(reverse("global_search"), {"q": "momo"}), "/tabellen/buecher/")
        self.table.in_nav = False
        self.table.save()
        self.login(self.it)
        self.assertNotContains(self.client.get(reverse("dashboard")), 'href="/tabellen/buecher/"')
