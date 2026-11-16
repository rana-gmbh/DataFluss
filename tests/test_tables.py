"""Own tables, step 2 (docs/TABLES-PLAN.md): design, values, rights, sync scope, backups and the bin."""
import json
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from archive import nightly, snapshot
from core.context_user import set_current_user
from core.models import AuditEvent, ChangeLog, Role
from sync.scope import Scope
from tables import services
from tables.models import EDIT, NONE, READ, Field, Record, Table
from vault import crypto

from .base import AppTestCase


class TableCase(AppTestCase):
    def setUp(self):
        set_current_user(self.it)
        self.table = services.create_table("Bücher")
        self.name = Field.objects.get(table=self.table, key="f_name")
        self.price = services.create_field(self.table, "Preis", "number", config={"decimals": 2, "unit": "€"})
        self.genre = services.create_field(self.table, "Genre", "choice", "Roman\nSachbuch")
        self.isbn = services.create_field(self.table, "ISBN", "text")
        self.isbn.unique = True
        self.isbn.save()


class DesignTests(TableCase):
    def test_keys_title_field_and_order(self):
        self.assertEqual(self.table.key, "buecher")
        self.assertEqual(self.table.title_field, "f_name")
        self.assertEqual(self.price.key, "f_preis")
        self.assertEqual(services.create_table("Bücher").key, "buecher_2")
        self.assertEqual(services.create_field(self.table, "Preis", "text").key, "f_preis_2")
        self.assertEqual([f.key for f in services.fields_of(self.table)][:3], ["f_name", "f_preis", "f_genre"])
        self.assertTrue(AuditEvent.objects.filter(action="table", summary__contains="Bücher").exists())

    def test_refusals(self):
        with self.assertRaisesMessage(ValidationError, "Name"):
            services.create_table(" ")
        with self.assertRaisesMessage(ValidationError, "Option"):
            services.create_field(self.table, "Farbe", "choice", "")
        with self.assertRaisesMessage(ValidationError, "Formel"):
            services.create_field(self.table, "Summe", "formula")
        for later in ("class", "erfunden"):
            with self.subTest(type=later), self.assertRaisesMessage(ValidationError, "Feldtyp"):
                services.create_field(self.table, "X", later)
        with self.assertRaisesMessage(ValidationError, "worauf"):
            services.create_field(self.table, "Verweis", "link")
        with self.assertRaisesMessage(ValidationError, "Nachkommastellen"):
            services.clean_config("number", {"decimals": "9"})

    def test_change_type_converts_values_and_refuses_what_does_not_fit(self):
        record = services.create_record(self.table, {"f_name": "Momo", "f_preis": "12,5"})
        services.change_type(self.price, "text")
        record.refresh_from_db()
        self.assertEqual(record.values["f_preis"], "12.50 €")
        with self.assertRaisesMessage(ValidationError, "neues Feld"):
            services.change_type(self.name, "date")

    def test_table_with_records_is_only_archived(self):
        services.create_record(self.table, {"f_name": "Momo"})
        with self.assertRaisesMessage(ValidationError, "Archivieren"):
            services.delete_table(self.table)
        empty = services.create_table("Leer")
        services.delete_table(empty)
        self.assertFalse(Table.objects.filter(pk=empty.pk).exists())


class ValueTests(TableCase):
    def test_values_are_stored_as_plain_json_and_shown_by_type(self):
        record = services.create_record(self.table, {"f_name": "Momo", "f_preis": "12,5", "f_genre": "Roman"})
        record.refresh_from_db()
        self.assertEqual(record.values, {"f_name": "Momo", "f_preis": "12.50", "f_genre": "Roman"})
        self.assertEqual(services.value(record, self.price), Decimal("12.50"))
        self.assertEqual(services.display(record, self.price), "12.50 €")
        self.assertEqual(record.title, "Momo")
        self.assertIn("roman", record.search_text)

    def test_set_value_validates_and_empty_removes_the_key(self):
        record = services.create_record(self.table, {"f_name": "Momo"})
        with self.assertRaisesMessage(ValidationError, "Unbekannte Auswahl"):
            services.set_value(record, self.genre, "Krimi")
        saved, previous = services.set_value(record, self.name, "Momo und die Zeitdiebe")
        self.assertEqual((saved, previous), ("Momo und die Zeitdiebe", "Momo"))
        self.assertEqual(Record.objects.get(pk=record.pk).title, "Momo und die Zeitdiebe")
        services.set_value(record, self.price, "3")
        services.set_value(record, self.price, "")
        self.assertNotIn("f_preis", Record.objects.get(pk=record.pk).values)

    def test_required_unique_and_archived_fields(self):
        self.name.required = True
        self.name.save()
        record = services.create_record(self.table, {"f_name": "Momo", "f_isbn": "978-3"})
        with self.assertRaisesMessage(ValidationError, "Pflichtfeld"):
            services.set_value(record, self.name, "")
        other = services.create_record(self.table, {"f_name": "Krabat"})
        with self.assertRaisesMessage(ValidationError, "schon ein anderer"):
            services.set_value(other, self.isbn, "978-3")
        services.set_value(record, self.isbn, "978-3")  # its own value again is fine
        self.price.archived = True
        self.price.save()
        with self.assertRaisesMessage(ValidationError, "archiviert"):
            services.set_value(record, self.price, "1")

    def test_secret_values_are_encrypted_masked_in_the_history_and_revealable(self):
        code = services.create_field(self.table, "Code", "secret")
        record = services.create_record(self.table, {"f_name": "Momo"})
        services.set_value(record, code, "geheim-4711")
        record.refresh_from_db()
        self.assertEqual(set(record.values["f_code"]), {"secret"})
        self.assertNotIn("geheim-4711", json.dumps(record.values))
        self.assertEqual(services.display(record, code), "••••••••")
        self.assertEqual(services.reveal(record, code), "geheim-4711")
        services.set_value(record, code, "••••••••")  # the placeholder changes nothing
        self.assertEqual(services.reveal(Record.objects.get(pk=record.pk), code), "geheim-4711")
        logged = ChangeLog.objects.get(record_id=record.pk, field="values.f_code")
        self.assertEqual(logged.new_value, "[verschlüsselt]")
        self.assertNotIn("geheim-4711", record.search_text)

    def test_check_record_finds_what_a_device_should_not_send(self):
        record = services.create_record(self.table, {"f_name": "Momo"})
        self.assertEqual(services.check_record(record), [])
        for bad in ({"f_genre": "Krimi"}, {"f_preis": "viel"}, {"f_erfunden": "x"}, {"f_name": 5}, {"f_preis": "1e400"}):
            with self.subTest(bad=bad):
                record.values = {"f_name": "Momo", **bad}
                self.assertTrue(services.check_record(record))
        record.values = {"f_name": "Momo", "f_preis": "12.50", "f_genre": "Roman"}
        self.assertEqual(services.check_record(record), [])


class RightTests(TableCase):
    def test_it_edits_roles_follow_the_table_archived_is_read_only(self):
        self.assertEqual(services.right(self.it, self.table), EDIT)
        self.assertEqual(services.right(self.sek, self.table), NONE)
        services.set_rights(self.table, {Role.SEKRETARIAT: EDIT, Role.SCHULLEITUNG: READ, Role.IT: NONE})
        self.assertEqual({k: v for k, v in self.table.rights.items() if v != NONE}, {Role.SEKRETARIAT: EDIT, Role.SCHULLEITUNG: READ})
        self.assertNotIn(Role.IT, self.table.rights)
        self.assertTrue(services.may_edit(self.sek, self.table))
        self.assertTrue(services.may_read(self.sl, self.table) and not services.may_edit(self.sl, self.table))
        self.table.archived = True
        self.table.save()
        self.assertEqual(services.right(self.sek, self.table), READ)
        self.assertEqual(services.readable(self.sek), [])


class PageTests(TableCase):
    def test_design_pages(self):
        self.login(self.it)
        self.assertContains(self.client.get(reverse("settings_home")), "Tabellen")
        self.assertContains(self.client.get(reverse("table_list")), "Bücher")
        answer = self.client.post(reverse("table_list"), {"name": "Ausleihen"})
        self.assertRedirects(answer, reverse("table_settings", args=["ausleihen"]))
        for tab in ("felder", "rechte", "allgemein"):
            self.assertEqual(self.client.get(reverse("table_settings", args=["buecher"]), {"tab": tab}).status_code, 200)
        self.client.post(reverse("table_field_new", args=["buecher"]), {"label": "Seiten", "type": "number", "in_grid": "on"})
        self.assertTrue(Field.objects.filter(table=self.table, key="f_seiten", type="number").exists())
        self.client.post(reverse("table_rights", args=["buecher"]), {f"right_{Role.SEKRETARIAT}": "edit", f"right_{Role.SCHULLEITUNG}": "erfunden"})
        self.table.refresh_from_db()
        self.assertEqual((self.table.rights[Role.SEKRETARIAT], self.table.rights[Role.SCHULLEITUNG]), (EDIT, NONE))
        page = self.client.get(reverse("table_field", args=["buecher", self.genre.pk]))
        self.assertContains(page, "Sachbuch")
        self.client.post(reverse("table_field", args=["buecher", self.genre.pk]), {"label": "Gattung", "option_value": ["Roman", "Sachbuch", ""],
                                                                                   "option_label": ["Roman", "Sachbuch", "Lyrik"], "in_grid": "on"})
        self.genre.refresh_from_db()
        self.assertEqual((self.genre.label, self.genre.option_values), ("Gattung", ["Roman", "Sachbuch", "Lyrik"]))
        answer = self.client.post(reverse("table_field_order", args=["buecher"]), json.dumps({"keys": ["f_isbn", "f_name"]}), content_type="application/json")
        self.assertEqual(answer.status_code, 200)
        self.assertEqual([f.key for f in services.fields_of(self.table)][:2], ["f_isbn", "f_name"])

    def test_general_settings_and_title_refresh(self):
        record = services.create_record(self.table, {"f_name": "Momo", "f_isbn": "978-3"})
        self.login(self.it)
        self.client.post(reverse("table_settings", args=["buecher"]) + "?tab=allgemein",
                         {"name": "Bibliothek", "icon": "book", "title_field": "f_isbn", "in_nav": "on"})
        self.table.refresh_from_db()
        self.assertEqual((self.table.name, self.table.icon, self.table.title_field), ("Bibliothek", "book", "f_isbn"))
        self.assertEqual(Record.objects.get(pk=record.pk).title, "978-3")

    def test_only_designers(self):
        self.login(self.sek)
        self.assertEqual(self.client.get(reverse("table_list")).status_code, 403)
        self.assertEqual(self.client.post(reverse("table_rights", args=["buecher"]), {}).status_code, 403)


class SyncScopeTests(TableCase):
    def setUp(self):
        super().setUp()
        self.code = services.create_field(self.table, "Code", "secret")
        self.record = services.create_record(self.table, {"f_name": "Momo"})
        services.set_value(self.record, self.code, "geheim-4711")
        self.hidden = services.create_table("Intern")
        self.hidden_record = services.create_record(self.hidden, {"f_name": "Nur IT"})

    def test_a_device_holds_only_readable_tables(self):
        services.set_rights(self.table, {Role.SCHULLEITUNG: READ})
        held = Scope(self.sl).records()
        self.assertIn(self.record.pk, held.get(Record, set()))
        self.assertIn(self.table.pk, held.get(Table, set()))
        self.assertNotIn(self.hidden_record.pk, held.get(Record, set()))
        self.assertNotIn(self.hidden.pk, held.get(Table, set()))
        self.assertNotIn(self.hidden_record.pk, Scope(self.sek).records().get(Record, set()))

    def test_secret_values_stay_off_devices_without_vault_access(self):
        services.set_rights(self.table, {Role.SCHULLEITUNG: READ, Role.SEKRETARIAT: EDIT})
        masked = Scope(self.sl).hide(Record)["values"](Record.objects.get(pk=self.record.pk).values)
        self.assertEqual(masked, {"f_name": "Momo"})
        self.assertIn("values.f_code", Scope(self.sl).ignored_keys(Record))
        self.assertEqual(Scope(self.sek).hide(Record), {})  # the secretariat sees passwords anyway

    def test_writes_need_the_edit_right_and_valid_values(self):
        services.set_rights(self.table, {Role.SCHULLEITUNG: READ, Role.SEKRETARIAT: EDIT})
        self.assertFalse(Scope(self.sl).may_write(Record, self.record.pk, ["values.f_name"]))
        self.assertTrue(Scope(self.sek).may_write(Record, self.record.pk, ["values.f_name"]))
        self.assertFalse(Scope(self.sek).may_write(Table, self.table.pk, ["name"]))
        self.assertTrue(Scope(self.it).may_write(Field, self.code.pk, ["label"]))
        self.assertTrue(Scope(self.sek).allows(Record, self.record.pk))
        Record.objects.filter(pk=self.record.pk).update(values={"f_name": "Momo", "f_preis": "teuer"})
        self.assertFalse(Scope(self.sek).allows(Record, self.record.pk))
        self.assertFalse(Scope(self.sek).allows(Record, self.hidden_record.pk))


class BackupAndBinTests(TableCase):
    def test_secret_values_travel_in_plain_text_inside_the_backup_only(self):
        code = services.create_field(self.table, "Code", "secret")
        record = services.create_record(self.table, {"f_name": "Momo"})
        services.set_value(record, code, "geheim-4711")
        field, rewrite = snapshot._secrets()[Record]
        stored = Record.objects.get(pk=record.pk).values
        plain = rewrite(stored, record.pk, crypto.decrypt)
        self.assertEqual(plain, {"f_name": "Momo", "f_code": {"secret": "geheim-4711"}})
        sealed = rewrite(plain, record.pk, crypto.encrypt)
        self.assertEqual(crypto.decrypt(sealed["f_code"]["secret"], f"record:{record.pk}:f_code"), "geheim-4711")
        self.assertEqual(field, "values")

    def test_recycled_records_are_emptied_after_the_keeping_time(self):
        record = services.create_record(self.table, {"f_name": "Momo"})
        record.recycle()
        self.assertFalse(Record.objects.filter(pk=record.pk).exists())
        Record.everything.filter(pk=record.pk).update(deleted_at=timezone.now() - timedelta(days=Record.KEEP_DAYS + 1))
        nightly.empty_bins()
        self.assertFalse(Record.everything.filter(pk=record.pk).exists())
