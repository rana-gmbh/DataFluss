"""The interface language, and names of fields, choices and masks per language."""
import json

from django.conf import settings
from django.urls import reverse
from django.utils import translation

from masks import i18n
from masks.models import FieldDefinition, Mask, MaskVersion
from masks.services import build_view, display_value
from masks.views import _clean_tree, create_field

from .base import AppTestCase


class RenameRulesTests(AppTestCase):
    def test_this_language_gets_its_own_name(self):
        base, names = i18n.rename("Hobby", {}, "Hobby (en)", i18n.THIS, "en")
        self.assertEqual((base, names), ("Hobby", {"en": "Hobby (en)"}))
        self.assertEqual(i18n.pick(base, names, "de"), "Hobby")
        self.assertEqual(i18n.pick(base, names, "zh-hans"), "Hobby")

    def test_all_languages_replace_the_base_and_drop_single_names(self):
        self.assertEqual(i18n.rename("Hobby", {"en": "Pastime"}, "Freizeit", i18n.ALL, "de"), ("Freizeit", {}))

    def test_unchanged_or_empty_name_changes_nothing(self):
        self.assertEqual(i18n.rename("Hobby", {"en": "Pastime"}, "Pastime", i18n.ALL, "en"), ("Hobby", {"en": "Pastime"}))
        self.assertEqual(i18n.rename("Hobby", {}, "  ", i18n.ALL, "de"), ("Hobby", {}))

    def test_back_to_the_base_drops_the_own_name(self):
        self.assertEqual(i18n.rename("Hobby", {"en": "Pastime"}, "Hobby", i18n.THIS, "en"), ("Hobby", {}))

    def test_option_keeps_its_stored_value(self):
        option = i18n.rename_option({"value": "Weiblich", "color": "#f00"}, "Female", i18n.THIS, "en")
        self.assertEqual(option, {"value": "Weiblich", "color": "#f00", "labels": {"en": "Female"}})
        option = i18n.rename_option(option, "weiblich", i18n.ALL, "de")
        self.assertEqual(option, {"value": "Weiblich", "color": "#f00", "label": "weiblich"})


class LanguageChoiceTests(AppTestCase):
    def test_choosing_a_language_is_kept_for_the_user(self):
        self.login(self.sek)
        response = self.client.post(reverse("set_language"), {"language": "en", "next": "/personen/schueler/"})
        self.assertRedirects(response, "/personen/schueler/", fetch_redirect_response=False)
        self.sek.refresh_from_db()
        self.assertEqual(self.sek.language, "en")
        self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, "en")
        page = self.client.get(reverse("dashboard"))
        self.assertEqual(page["Content-Language"], "en")
        self.assertContains(page, '<html lang="en">')

    def test_unknown_language_and_foreign_redirect_are_refused(self):
        self.login(self.sek)
        self.assertEqual(self.client.post(reverse("set_language"), {"language": "xx"}).status_code, 400)
        response = self.client.post(reverse("set_language"), {"language": "de", "next": "https://example.com/"})
        self.assertEqual(response["Location"], "/")

    def test_the_browser_language_does_not_switch_by_itself(self):
        self.login(self.sek)
        page = self.client.get(reverse("dashboard"), HTTP_ACCEPT_LANGUAGE="zh-CN,zh;q=0.9")
        self.assertEqual(page["Content-Language"], "de")

    def test_sign_in_page_language_becomes_the_users(self):
        self.client.post(reverse("set_language"), {"language": "zh-hant"})
        self.client.post(reverse("login"), {"username": "sl", "password": "x" * 14})
        self.sl.refresh_from_db()
        self.assertEqual(self.sl.language, "zh-hant")

    def test_remembering_the_rename_answer(self):
        self.login(self.it)
        response = self.client.post(reverse("name_scope"), json.dumps({"scope": "this"}), content_type="application/json")
        self.assertEqual(response.json(), {"ok": True, "scope": "this"})
        self.it.refresh_from_db()
        self.assertEqual(self.it.name_scope, "this")
        self.assertEqual(self.client.post(reverse("name_scope"), json.dumps({"scope": "x"}), content_type="application/json").status_code, 400)

    def test_script_texts_load_without_signing_in(self):
        self.assertEqual(self.client.get(reverse("js_catalog"), {"l": "en"}).status_code, 200)



class AllowedLanguagesTests(AppTestCase):
    def allow(self, *languages):
        from core.models import SchoolSettings

        SchoolSettings.load()
        SchoolSettings.objects.filter(pk=1).update(languages=list(languages))

    def test_it_chooses_the_languages_in_settings(self):
        from core.models import SchoolSettings

        self.login(self.it)
        self.assertContains(self.client.get(reverse("settings_home")), reverse("school_languages"))
        self.assertEqual(self.client.get(reverse("school_languages")).status_code, 200)
        self.client.post(reverse("school_languages"), {"languages": ["en", "zh-hans"]})
        self.assertEqual(SchoolSettings.load().languages, ["en", "zh-hans"])
        self.client.post(reverse("school_languages"), {"languages": ["de", "en", "zh-hans", "zh-hant"]})
        self.assertEqual(SchoolSettings.load().languages, [])  # all of them: no restriction
        self.client.post(reverse("school_languages"), {})
        self.assertEqual(SchoolSettings.load().languages, [])  # at least one stays

    def test_only_it_may_change_them(self):
        self.login(self.sek)
        self.assertEqual(self.client.get(reverse("school_languages")).status_code, 403)
        self.assertEqual(self.client.post(reverse("school_languages"), {"languages": ["en"]}).status_code, 403)

    def test_a_language_no_longer_allowed_falls_back(self):
        self.sek.language = "zh-hant"
        self.sek.save()
        self.allow("de", "en")
        self.login(self.sek)
        page = self.client.get(reverse("dashboard"))
        self.assertEqual(page["Content-Language"], "de")
        self.assertEqual(self.client.post(reverse("set_language"), {"language": "zh-hant"}).status_code, 400)
        self.assertEqual(self.client.post(reverse("language_settings"), {"language": "zh-hant", "name_scope": ""}).status_code, 400)
        self.sek.refresh_from_db()
        self.assertEqual(self.sek.language, "zh-hant")  # kept for when it is allowed again
        self.allow()
        self.assertEqual(self.client.get(reverse("dashboard"))["Content-Language"], "zh-hant")

    def test_without_german_the_first_allowed_language_is_used(self):
        self.allow("zh-hans", "en")
        self.assertEqual(self.client.get(reverse("login"))["Content-Language"], "en")
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "de"
        self.assertEqual(self.client.get(reverse("login"))["Content-Language"], "en")

    def test_one_language_hides_the_menu_and_the_rename_question(self):
        self.allow("en")
        self.login(self.it)
        page = self.client.get(reverse("dashboard"))
        self.assertEqual(page["Content-Language"], "en")
        self.assertContains(page, 'data-single="1"')
        self.assertNotContains(page, reverse("set_language"))
        self.assertNotContains(self.client.get(reverse("language_settings")), 'name="name_scope" value="all"')
        # A remembered "all" does not apply: the languages nobody sees keep their names.
        self.it.name_scope = "all"
        self.it.save()
        field = FieldDefinition.objects.get(entity="pupil", key="gender")
        with translation.override("en"):
            shown = [field.option_label(v) for v in field.option_values]
        self.client.post(reverse("field_edit", args=[field.pk]), {"label": "Sex", "option_value": field.option_values, "option_label": shown})
        field.refresh_from_db()
        self.assertEqual(field.label, "Geschlecht")
        self.assertEqual(field.labels["en"], "Sex")
        self.assertEqual(field.labels["zh-hans"], "性别")

    def test_connected_macs_get_the_choice(self):
        from sync.server import _school_payload

        self.allow("en")
        self.assertEqual(_school_payload()["languages"], ["en"])


class BuiltInNamesTests(AppTestCase):
    def test_built_in_fields_choices_and_masks_come_translated(self):
        field = FieldDefinition.objects.get(entity="pupil", key="gender")
        mask = Mask.objects.get(entity="pupil", is_default=True)
        with translation.override("en"):
            self.assertEqual(field.display_label, "Gender")
            self.assertEqual(field.option_label("Weiblich"), "Female")
            self.assertEqual(mask.display_name, "Pupils")
        with translation.override("zh-hant"):
            self.assertEqual(field.display_label, "性別")
        with translation.override("de"):
            self.assertEqual(field.display_label, "Geschlecht")

    def test_record_shows_tabs_sections_and_choices_in_the_language(self):
        self.pupil.gender = "Weiblich"
        self.pupil.save()
        tree = Mask.objects.get(entity="pupil", is_default=True).published.tree
        with translation.override("en"):
            tabs = build_view(tree, self.pupil, self.it)
            self.assertEqual(tabs[0]["title"], "Basic data")
            self.assertEqual(tabs[0]["sections"][0]["title"], "General")
            items = {i["fd"].key: i for i in tabs[0]["sections"][0]["items"] if i["type"] == "field"}
            self.assertEqual(items["gender"]["label"], "Gender")
            self.assertEqual(items["gender"]["display"], "Female")
            self.assertIn(("Weiblich", "Female"), items["gender"]["choices"])
        # Rules compare the stored value, whatever the language
        gender = FieldDefinition.objects.get(entity="pupil", key="gender")
        with translation.override("en"):
            self.assertEqual(display_value(self.pupil, gender), "Weiblich")


class FieldRenameTests(AppTestCase):
    def setUp(self):
        self.login(self.it)
        self.field = create_field("pupil", "Hobby", "choice", "Fußball\nSchach")

    def save(self, language, **data):
        self.client.post(reverse("set_language"), {"language": language})
        payload = {"label": self.field.display_label, "option_value": ["Fußball", "Schach"], "option_label": ["Fußball", "Schach"], **data}
        return self.client.post(reverse("field_edit", args=[self.field.pk]), payload)

    def test_german_without_own_names_just_renames(self):
        self.save("de", label="Freizeit")
        self.field.refresh_from_db()
        self.assertEqual((self.field.label, self.field.labels), ("Freizeit", {}))

    def test_english_only_keeps_the_original_elsewhere(self):
        self.save("en", label="Pastime", option_label=["Football", "Schach"], name_scope="this")
        self.field.refresh_from_db()
        self.assertEqual((self.field.label, self.field.labels), ("Hobby", {"en": "Pastime"}))
        self.assertEqual(self.field.option_values, ["Fußball", "Schach"])
        with translation.override("en"):
            self.assertEqual(self.field.option_label("Fußball"), "Football")
        with translation.override("zh-hans"):
            self.assertEqual(self.field.display_label, "Hobby")
            self.assertEqual(self.field.option_label("Fußball"), "Fußball")

    def test_all_languages_replace_own_names(self):
        self.field.labels = {"en": "Pastime", "zh-hans": "爱好"}
        self.field.save()
        self.save("en", label="Leisure", name_scope="all")
        self.field.refresh_from_db()
        self.assertEqual((self.field.label, self.field.labels), ("Leisure", {}))

    def test_remembered_answer_applies_without_the_dialog(self):
        self.it.name_scope = "this"
        self.it.save()
        self.save("zh-hans", label="爱好")
        self.field.refresh_from_db()
        self.assertEqual((self.field.label, self.field.labels), ("Hobby", {"zh-hans": "爱好"}))

    def test_other_language_without_an_answer_only_renames_that_language(self):
        self.save("en", label="Pastime")
        self.field.refresh_from_db()
        self.assertEqual((self.field.label, self.field.labels), ("Hobby", {"en": "Pastime"}))

    def test_records_keep_their_choice_when_an_option_is_renamed(self):
        self.pupil.custom[self.field.key] = "Schach"
        self.pupil.save()
        self.save("de", option_label=["Fußball", "Chess club"])
        self.field.refresh_from_db()
        self.assertEqual(self.field.option_values, ["Fußball", "Schach"])
        self.assertEqual(self.field.option_label("Schach"), "Chess club")

    def test_options_can_be_added_and_removed(self):
        self.save("de", option_value=["Schach", ""], option_label=["Schach", "Tennis"])
        self.field.refresh_from_db()
        self.assertEqual(self.field.option_values, ["Schach", "Tennis"])

    def test_old_one_per_line_form_still_works(self):
        self.client.post(reverse("field_edit", args=[self.field.pk]), {"label": "Hobby", "options": "Fußball\nSchach\nTennis"})
        self.field.refresh_from_db()
        self.assertEqual(self.field.option_values, ["Fußball", "Schach", "Tennis"])

    def test_the_form_asks_when_names_exist(self):
        self.field.labels = {"en": "Pastime"}
        self.field.save()
        page = self.client.get(reverse("field_edit", args=[self.field.pk]))
        self.assertContains(page, 'data-has-translations="1"')
        self.assertContains(page, "data-name-input")


class MaskNamesTests(AppTestCase):
    def setUp(self):
        self.login(self.it)
        self.mask = Mask.objects.get(entity="pupil", is_default=True)

    def test_renaming_a_mask_for_one_language(self):
        self.client.post(reverse("set_language"), {"language": "en"})
        self.client.post(reverse("mask_action", args=[self.mask.pk]), {"action": "rename", "name": "Students", "name_scope": "this"})
        self.mask.refresh_from_db()
        self.assertEqual(self.mask.name, "Schüler")
        self.assertEqual(self.mask.names["en"], "Students")
        self.assertEqual(self.mask.names["zh-hans"], "学生")

    def test_saved_trees_keep_names_per_language(self):
        tree = {"tabs": [{"id": "t1", "title": "Daten", "titles": {"en": "Data", "xx": "?", "zh-hans": ""}, "sections": [
            {"id": "s1", "title": "", "titles": {"en": "General"}, "items": [
                {"type": "field", "field": "first_name", "w": 6, "labels": {"en": "Given name"}}]}]}]}
        clean = _clean_tree(tree)
        self.assertEqual(clean["tabs"][0]["titles"], {"en": "Data"})
        self.assertEqual(clean["tabs"][0]["sections"][0]["titles"], {"en": "General"})
        self.assertEqual(clean["tabs"][0]["sections"][0]["items"][0]["labels"], {"en": "Given name"})
        with translation.override("en"):
            tabs = build_view(clean, self.pupil, self.it)
        self.assertEqual(tabs[0]["title"], "Data")
        self.assertEqual(tabs[0]["sections"][0]["items"][0]["label"], "Given name")

    def test_a_changed_shipped_name_gets_no_translations(self):
        from masks.builtin_names import fill_tree

        tree = {"tabs": [{"id": "t1", "title": "Unsere Daten", "sections": [{"id": "s1", "title": "Allgemeine Daten", "items": []}]}]}
        self.assertTrue(fill_tree(tree))
        self.assertNotIn("titles", tree["tabs"][0])
        self.assertEqual(tree["tabs"][0]["sections"][0]["titles"]["en"], "General")

    def test_builder_and_list_show_the_language(self):
        self.client.post(reverse("set_language"), {"language": "en"})
        self.assertContains(self.client.get(reverse("mask_list")), "Pupils")
        page = self.client.get(reverse("mask_builder", args=[self.mask.pk]))
        self.assertContains(page, "Pupils")
        self.assertContains(page, "builder-rename")
        MaskVersion.objects.filter(mask=self.mask).exists()


class LanguageSettingsPageTests(AppTestCase):
    def test_changing_language_and_rename_answer(self):
        self.login(self.sek)
        self.assertEqual(self.client.get(reverse("language_settings")).status_code, 200)
        self.client.post(reverse("language_settings"), {"language": "zh-hans", "name_scope": "all"})
        self.sek.refresh_from_db()
        self.assertEqual((self.sek.language, self.sek.name_scope), ("zh-hans", "all"))
        self.client.post(reverse("language_settings"), {"language": "zh-hans", "name_scope": ""})
        self.sek.refresh_from_db()
        self.assertEqual(self.sek.name_scope, "")
