"""Signed module bundles: a module delivered as a file instead of being part of the build.

A bundle `<id>-<version>.dfmodule` is a zip with the module's Django apps (same package names as in
the source, so app labels and migrations do not change), their templates, static files and
translations, plus

    manifest.json   {"format", "id", "version", "title", "requires", "apps", "stylesheet", "files": {path: sha256}}
    manifest.sig    the Ed25519 signature of manifest.json, base64, by a key in TRUSTED_KEYS

DATAFLUSS_MODULE_PATH names folders (os.pathsep between them) that hold bundles. Before Django starts,
load() unpacks new bundle files there into <id>/<version>/, checks the signature and every file's hash,
and makes the bundles of this DataFluss version importable. A module that is part of the build wins over
a bundle of it. Nothing here may import Django: the settings run it (schule/modules.py).
"""
import base64
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

FORMAT = 1
EXTENSION = ".dfmodule"
# Public halves of the keys that sign modules. The private half of Rana GmbH's key is kept like the
# Sparkle key (docs/releases.md) and signs in the module workflow.
TRUSTED_KEYS = ["9wdY1vqTqR+Z+TszoLm6bTGqztuPg5stC7FF/hw7k1E="]

LOADED = {}  # id -> {"path": Path, "manifest": dict}, filled by load()
_done = False


class BundleError(Exception):
    pass


def platform_version():
    path = Path(__file__).resolve().parent.parent / "VERSION"
    return path.read_text().strip() if path.exists() else "dev"


def trusted_keys():
    keys = list(TRUSTED_KEYS)
    if os.environ.get("TESTING") and os.environ.get("DATAFLUSS_MODULE_KEYS"):  # tests sign with a key of their own
        keys += os.environ["DATAFLUSS_MODULE_KEYS"].split(",")
    return keys


def folders():
    return [Path(p) for p in os.environ.get("DATAFLUSS_MODULE_PATH", "").split(os.pathsep) if p]


def _verify_signature(manifest_bytes, signature):
    from nacl.exceptions import BadSignatureError
    from nacl.signing import VerifyKey

    raw = base64.b64decode(signature.strip())
    for key in trusted_keys():
        try:
            VerifyKey(base64.b64decode(key)).verify(manifest_bytes, raw)
            return
        except (BadSignatureError, ValueError):
            continue
    raise BundleError("the signature is not from a trusted key")


def _check_manifest(manifest):
    if manifest.get("format") != FORMAT:
        raise BundleError("unknown bundle format")
    for key in ("id", "version", "apps", "files"):
        if not manifest.get(key):
            raise BundleError(f"the manifest lacks {key}")
    for path in manifest["files"]:
        parts = Path(path).parts
        if Path(path).is_absolute() or ".." in parts or parts[0] not in manifest["apps"]:
            raise BundleError(f"a file outside the module's apps: {path}")


def read_archive(archive):
    """Checks a .dfmodule file and returns its manifest. The archive itself stays as it is."""
    with zipfile.ZipFile(archive) as zf:
        manifest_bytes = zf.read("manifest.json")
        _verify_signature(manifest_bytes, zf.read("manifest.sig").decode())
        manifest = json.loads(manifest_bytes)
        _check_manifest(manifest)
        for path, digest in manifest["files"].items():
            if hashlib.sha256(zf.read(path)).hexdigest() != digest:
                raise BundleError(f"{path} does not match the manifest")
        extra = set(zf.namelist()) - set(manifest["files"]) - {"manifest.json", "manifest.sig"}
        if extra:
            raise BundleError(f"files the manifest does not list: {', '.join(sorted(extra)[:3])}")
    return manifest


def unpack(archive, folder):
    """Checks an archive and unpacks it to folder/<id>/<version>/. Returns that path."""
    manifest = read_archive(archive)
    target = Path(folder) / manifest["id"] / manifest["version"]
    if not target.exists():
        staging = Path(tempfile.mkdtemp(prefix=".unpack-", dir=folder))
        try:
            with zipfile.ZipFile(archive) as zf:
                for name in [*manifest["files"], "manifest.json", "manifest.sig"]:
                    destination = staging / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(zf.read(name))
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                staging.rename(target)  # atomic, so a second process starting at the same time sees all or nothing
            except OSError:
                if not target.exists():
                    raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    # The archive stays beside the unpacked bundle: a server hands it out to its computers (sync/views.py).
    keep = Path(folder) / f"{manifest['id']}-{manifest['version']}{EXTENSION}"
    if Path(archive).resolve() != keep.resolve():
        shutil.copyfile(archive, keep)
    return target


def verify_folder(path):
    """Checks an unpacked bundle: the signature and every file. Returns its manifest."""
    manifest_bytes = (path / "manifest.json").read_bytes()
    _verify_signature(manifest_bytes, (path / "manifest.sig").read_text())
    manifest = json.loads(manifest_bytes)
    _check_manifest(manifest)
    for name, digest in manifest["files"].items():
        if hashlib.sha256((path / name).read_bytes()).hexdigest() != digest:
            raise BundleError(f"{name} was changed")
    return manifest


def archive_for(module_id, version):
    """The .dfmodule file of a loaded or kept bundle, for handing it out, or None."""
    for folder in folders():
        candidate = folder / f"{module_id}-{version}{EXTENSION}"
        if candidate.is_file():
            return candidate
    return None


def _warn(text):
    print(f"datafluss modules: {text}", file=sys.stderr)


def load(built_in):
    """Unpacks new archives and makes this version's bundles importable. `built_in(id)` says whether a
    module is part of the build; such a module is not loaded from a bundle. Returns the manifests loaded."""
    global _done
    if _done:
        return LOADED
    _done = True
    version = platform_version()
    for folder in folders():
        if not folder.is_dir():
            continue
        for archive in sorted(folder.glob(f"*{EXTENSION}")):
            try:
                unpack(archive, folder)
            except (BundleError, zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
                _warn(f"{archive.name} refused: {exc}")
        for path in sorted(p for p in folder.glob(f"*/{version}") if p.is_dir()):
            module_id = path.parent.name
            if module_id in LOADED or built_in(module_id):
                continue
            try:
                manifest = verify_folder(path)
            except (BundleError, KeyError, ValueError, OSError) as exc:
                _warn(f"{module_id} {version} refused: {exc}")
                continue
            if manifest["id"] != module_id or manifest["version"] != version:
                _warn(f"{path} holds {manifest['id']} {manifest['version']}")
                continue
            sys.path.append(str(path))
            LOADED[module_id] = {"path": path, "manifest": manifest}
    return LOADED


def static_folders():
    """The static folders of the loaded bundles' apps, served by core.middleware.ModuleStaticMiddleware."""
    return [info["path"] / app / "static" for info in LOADED.values() for app in info["manifest"]["apps"]
            if (info["path"] / app / "static").is_dir()]


def stylesheets():
    """The stylesheets the loaded bundles bring (their utilities are not in the platform's own CSS)."""
    return [info["manifest"]["stylesheet"] for info in LOADED.values() if info["manifest"].get("stylesheet")]


def reset():
    """For tests: forget what was loaded."""
    global _done
    _done = False
    for info in LOADED.values():
        if str(info["path"]) in sys.path:
            sys.path.remove(str(info["path"]))
    LOADED.clear()
