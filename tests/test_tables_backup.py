"""Own tables in backups (docs/TABLES-PLAN.md §11): tables, links, files and secret values come back, also on another key."""
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image

from archive import snapshot
from core.context_user import set_current_user
from masks.models import Mask
from tables import blueprints, links, masks as table_masks, services
from tables.models import Attachment, Field, Link, Record, Table
from vault import crypto

from .test_archive import OTHER_KEY, PASSWORD, ArchiveCase
from .test_blueprints import LIBRARY


class TablesBackupTests(ArchiveCase):
    def setUp(self):
        super().setUp()
        set_current_user(self.it)
        blueprints.apply(LIBRARY, self.it, demo=True)
        self.books = Table.objects.get(key="buecher")
        self.loans = Table.objects.get(key="ausleihen")
        self.code = services.create_field(self.books, "Schrankcode", "secret")
        self.cover = services.create_field(self.books, "Umschlag", "image")
        self.momo = Record.objects.get(table=self.books, title="Momo")
        services.set_value(self.momo, self.code, "Schrank-4711")
        image = io.BytesIO()
        Image.new("RGB", (4, 4), "green").save(image, "PNG")
        self.file = links.add_file(self.momo, self.cover, SimpleUploadedFile("u.png", image.getvalue(), content_type="image/png"))
        self.loan = Record.objects.filter(table=self.loans).first()
        links.set_targets(self.loan, Field.objects.get(table=self.loans, key="f_schueler"), [str(self.pupil.pk)])
        table_masks.ensure_mask(self.books, self.it)

    def test_tables_links_files_and_secrets_come_back_on_another_key(self):
        path = self.container()
        manifest = snapshot.read_manifest(path, PASSWORD)
        self.assertEqual(manifest["rows"]["tables.record"], Record.everything.count())
        self.assertNotIn(b"Schrank-4711", path.read_bytes())
        counts = (Table.objects.count(), Field.objects.count(), Record.everything.count(), Link.objects.count(), Mask.objects.filter(table__isnull=False).count())
        Record.everything.filter(table=self.loans).delete()
        with override_settings(VAULT_KEY=OTHER_KEY):
            crypto.reset_key_cache()
            self.restore(path)
            self.assertEqual((Table.objects.count(), Field.objects.count(), Record.everything.count(), Link.objects.count(),
                              Mask.objects.filter(table__isnull=False).count()), counts)
            momo = Record.objects.get(pk=self.momo.pk)
            self.assertEqual(services.reveal(momo, Field.objects.get(pk=self.code.pk)), "Schrank-4711")
            attachment = Attachment.objects.get(pk=self.file.pk)
            self.assertTrue(attachment.file.storage.exists(attachment.file.name))
            loan = Record.objects.get(pk=self.loan.pk)
            self.assertEqual(links.targets(loan, Field.objects.get(table=self.loans, key="f_schueler")), [self.pupil.pk])
            self.assertEqual(links.backlinks(self.it, "record", momo.pk)[0]["title"], "Ausleihen")
        crypto.reset_key_cache()
