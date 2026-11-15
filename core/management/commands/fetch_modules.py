"""Fetches this version's module bundles from the GitHub release, so the server can hand them out.

    DATAFLUSS_MODULE_PATH=/data/modules DATAFLUSS_MODULE_FETCH=premium python manage.py fetch_modules

Run by deploy/start.sh. The bundle `<id>-<VERSION>.dfmodule` is an asset of the release v<VERSION> of
DATAFLUSS_MODULE_REPOSITORY (default GITHUB_REPOSITORY), read with GITHUB_TOKEN. A bundle is kept only
if its signature holds (schule/bundles.py). Never fatal: without it the server works as before, it just
has no bundle for computers that lack the module.
"""
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core.updates import _request
from schule import bundles


class Command(BaseCommand):
    help = "Downloads this version's module bundles from the GitHub release."

    def handle(self, *args, **options):
        import os

        wanted = [m.strip() for m in os.environ.get("DATAFLUSS_MODULE_FETCH", "").split(",") if m.strip()]
        folders = bundles.folders()
        if not wanted or not folders:
            return
        version = settings.DATAFLUSS_VERSION
        repository = os.environ.get("DATAFLUSS_MODULE_REPOSITORY") or settings.GITHUB_REPOSITORY
        folder = folders[0]
        folder.mkdir(parents=True, exist_ok=True)
        todo = [m for m in wanted if bundles.archive_for(m, version) is None]
        if not todo:
            return
        try:
            release = _request(f"https://api.github.com/repos/{repository}/releases/tags/{urllib.parse.quote(f'v{version}')}", settings.GITHUB_TOKEN)
        except Exception as exc:  # noqa: BLE001 - network or missing release; the server starts anyway
            self.stderr.write(f"Module: keine Freigabe v{version} in {repository} ({exc})")
            return
        assets = {asset["name"]: asset for asset in release.get("assets", [])}
        for module_id in todo:
            asset = assets.get(f"{module_id}-{version}{bundles.EXTENSION}")
            if asset is None:
                self.stderr.write(f"Module: {module_id} {version} fehlt in der Freigabe")
                continue
            try:
                self._download(asset["url"], folder, asset["name"])
                self.stdout.write(f"Module: {asset['name']} geladen")
            except Exception as exc:  # noqa: BLE001
                self.stderr.write(f"Module: {asset['name']} nicht geladen ({exc})")

    def _download(self, url, folder, name):
        headers = {"Accept": "application/octet-stream", "User-Agent": "datafluss"}
        if settings.GITHUB_TOKEN:
            headers["Authorization"] = f"Bearer {settings.GITHUB_TOKEN}"
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as response, \
                tempfile.NamedTemporaryFile(dir=folder, suffix=".part", delete=False) as part:
            part.write(response.read())
        path = Path(part.name)
        try:
            bundles.unpack(path, folder)  # checks the signature, keeps <id>-<version>.dfmodule beside it
        finally:
            path.unlink(missing_ok=True)
