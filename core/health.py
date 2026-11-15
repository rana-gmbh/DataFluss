"""What IT should know without being asked. Shown on the start page."""
import shutil
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import AuditEvent

LOW_SHARE = 0.1
ENOUGH_ANYWAY = 10 * 1024 ** 3


def warnings_for(user):
    from archive import nightly

    if not user.can("admin.backup"):
        return []
    rows = []
    backup = nightly.summary()
    if backup["level"] != "ok":
        rows.append({**backup, "url": reverse("backups")})
    root = Path(settings.BACKUP_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    disk = shutil.disk_usage(root)
    if disk.free < min(disk.total * LOW_SHARE, ENOUGH_ANYWAY):
        rows.append({"level": "bad", "text": _("Auf dem Server sind nur noch %(gb)s GB frei.") % {"gb": disk.free // 1024 ** 3}, "url": reverse("backups")})
    errors = AuditEvent.objects.filter(action="server_error", at__gte=timezone.now() - timedelta(hours=24)).count()
    if errors:
        rows.append({"level": "bad", "text": _("Serverfehler in den letzten 24 Stunden: %(n)s.") % {"n": errors}, "url": f"{reverse('audit_log')}?aktion=server_error"})
    return rows
