"""Which modules exist and which Django apps each brings (docs/modules.md).

Read by the settings, the URLs and the sidecar build, so nothing here may import Django.

    platform         the DataFluss platform: accounts, masks, prints, import and export, backups, sync, vault
    schulverwaltung  the basic module Schulverwaltung: people, classes, devices

DATAFLUSS_MODULES lists the optional modules to load, comma-separated ("premium" or ""). Without it,
every module whose code is present is loaded. Platform and Schulverwaltung are always loaded: the
platform's apps still refer to people and devices, until the table engine (phase 7) separates them.

A module may also come as a signed bundle (schule/bundles.py) from a folder in DATAFLUSS_MODULE_PATH, or,
for its own development, from source (DATAFLUSS_MODULE_SOURCES, see _load_sources).
"""
import os
from importlib.util import find_spec

from . import bundles

MODULES = {
    "platform": ("core", "vault", "masks", "prints", "transfer", "archive", "sync"),
    "schulverwaltung": ("people", "devices"),
}
REQUIRED = ("platform", "schulverwaltung")
# Names shown when a database or backup needs a module that is not loaded.
TITLES = {
    "platform": "DataFluss",
    "schulverwaltung": "Schulverwaltung",
}

# The order of INSTALLED_APPS and of the URL includes, unchanged from before the modules: templates,
# static files and URL names resolve in this order.
ORDER = [
    "core", "people", "devices", "vault", "masks", "prints", "transfer", "archive",
    "sync",
]


def present(name):
    return all(find_spec(app) is not None for app in MODULES[name])


def _load_sources():
    """Modules from source for their own development and tests: DATAFLUSS_MODULE_SOURCES names folders with a
    datafluss-module.json ({"id", "title", "apps"}) beside the module's apps. Not signed: whoever sets the
    variable runs the code, so servers and the desktop apps load modules only from signed bundles."""
    import json
    import sys
    from pathlib import Path

    for folder in (Path(p) for p in os.environ.get("DATAFLUSS_MODULE_SOURCES", "").split(os.pathsep) if p):
        descriptor = folder / "datafluss-module.json"
        if not descriptor.is_file():
            continue
        module = json.loads(descriptor.read_text())
        if module["id"] in MODULES:
            continue
        if str(folder) not in sys.path:
            sys.path.append(str(folder))
        MODULES[module["id"]] = tuple(module["apps"])
        TITLES[module["id"]] = module.get("title") or module["id"]
        ORDER[ORDER.index("sync"):ORDER.index("sync")] = [app for app in module["apps"] if app not in ORDER]


def _load_bundles():
    """Signed bundles of modules this build lacks become importable and known here (once per process)."""
    _load_sources()
    for module_id, info in bundles.load(built_in=lambda name: name in MODULES and present(name)).items():
        manifest = info["manifest"]
        if module_id not in MODULES:
            MODULES[module_id] = tuple(manifest["apps"])
            TITLES[module_id] = manifest.get("title") or module_id
            ORDER[ORDER.index("sync"):ORDER.index("sync")] = [app for app in manifest["apps"] if app not in ORDER]


def from_bundle(name):
    """Whether a loaded module came from a bundle rather than the build."""
    return name in bundles.LOADED


def selected():
    """The modules to load, in the order of MODULES."""
    _load_bundles()
    raw = os.environ.get("DATAFLUSS_MODULES")
    if raw is None:
        wanted = {name for name in MODULES if present(name)}
    else:
        wanted = {name.strip() for name in raw.split(",") if name.strip()}
        unknown = wanted - set(MODULES)
        if unknown:
            raise ValueError(f"DATAFLUSS_MODULES names unknown modules: {', '.join(sorted(unknown))}")
    wanted.update(REQUIRED)
    return [name for name in MODULES if name in wanted]


def apps(modules=None):
    """The Django apps of the given (or the selected) modules, in ORDER."""
    chosen = {app for name in (modules or selected()) for app in MODULES[name]}
    return [app for app in ORDER if app in chosen]


def installed(name):
    """Whether a module is loaded in this process."""
    return name in selected()


def module_of(app):
    """The module an app belongs to, or None for an app no module names (Django's own, say)."""
    return next((name for name, members in MODULES.items() if app in members), None)
