"""Builds and signs a module bundle (schule/bundles.py) from the module's apps in this repository.

    .venv/bin/python scripts/build_module.py premium                 # dist/modules/premium-<VERSION>.dfmodule
    .venv/bin/python scripts/build_module.py premium --out somewhere --key path/to/key

The signing key is the base64 Ed25519 seed in DATAFLUSS_MODULE_SIGNING_KEY or in the file given with
--key (default .secrets/module-signing.key). The bundle carries the apps without their tests, the
project's compiled translations, and a stylesheet with the Tailwind utilities its templates use
(built with the frontend's Tailwind, so `cd frontend && npm install` first; --no-css skips it).
"""
import argparse
import base64
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from schule import bundles, modules  # noqa: E402

modules.selected()  # a module from source (DATAFLUSS_MODULE_SOURCES) joins the catalog

SKIP_DIRS = {"__pycache__", "tests", "node_modules"}
SKIP_SUFFIXES = {".pyc", ".pyo"}


def app_dir(app):
    """Where an app's package is: in this repository, or in a module's own (DATAFLUSS_MODULE_SOURCES)."""
    from importlib.util import find_spec

    spec = find_spec(app)
    if spec is None or not spec.origin:
        sys.exit(f"App {app} not found")
    return Path(spec.origin).parent


def app_files(app):
    base = app_dir(app)
    for path in sorted(base.rglob("*")):
        relative = path.relative_to(base.parent)
        if path.is_dir() or SKIP_DIRS & set(relative.parts) or path.suffix in SKIP_SUFFIXES or path.name == ".DS_Store":
            continue
        yield relative.as_posix(), path.read_bytes()


def translations(app):
    """The module's compiled translations (locale/ beside its apps), else the project's."""
    own = app_dir(app).parent / "locale"
    folder = own if own.is_dir() and own != ROOT / "locale" else ROOT / "locale"
    for mo in sorted(folder.glob("*/LC_MESSAGES/*.mo")):
        yield f"{app}/locale/{mo.relative_to(folder).as_posix()}", mo.read_bytes()


def stylesheet(module_id, apps):
    """Tailwind's theme and the utilities the module's templates and scripts use, nothing else."""
    app_css = (ROOT / "frontend" / "app.css").read_text()
    theme = re.search(r"@theme \{.*?\n\}", app_css, re.S).group(0)
    variants = "\n".join(re.findall(r"^@custom-variant .*$", app_css, re.M))
    folders = [app_dir(app) / part for app in apps for part in ("templates", "static/js") if (app_dir(app) / part).is_dir()]
    source = ('@import "tailwindcss/theme.css" layer(theme);\n@import "tailwindcss/utilities.css" layer(utilities) source(none);\n'
              f"{{sources}}\n{variants}\n{theme}\n")
    with tempfile.TemporaryDirectory(dir=ROOT / "frontend") as work:  # inside frontend/, where Tailwind is installed
        entry, output = Path(work) / "module.css", Path(work) / "out.css"
        sources = "\n".join(f'@source "{os.path.relpath(folder, work)}";' for folder in folders)
        entry.write_text(source.replace("{sources}", sources))
        subprocess.run(["npx", "@tailwindcss/cli", "-i", str(entry), "-o", str(output), "--minify"], cwd=ROOT / "frontend",
                       check=True, capture_output=True)
        return output.read_bytes()


def signing_key(path):
    from nacl.signing import SigningKey

    raw = os.environ.get("DATAFLUSS_MODULE_SIGNING_KEY") or Path(path).read_text()
    return SigningKey(base64.b64decode(raw.strip()))


def build(module_id, out, key_path, css=True, version=None):
    if module_id not in modules.MODULES or module_id in modules.REQUIRED:
        sys.exit(f"Unknown optional module: {module_id}")
    apps = list(modules.MODULES[module_id])
    version = version or bundles.platform_version()
    files = {}
    for app in apps:
        files.update(app_files(app))
    if apps:
        files.update(translations(apps[0]))
    templates = []
    for path in modules.TEMPLATE_FILES.get(module_id, []):  # module templates travel under templates/
        name = f"templates/{Path(path).name}"
        files[name] = Path(path).read_bytes()
        templates.append(name)
    sheet = ""
    if css and apps:
        sheet = f"css/{module_id}.css"
        files[f"{apps[0]}/static/{sheet}"] = stylesheet(module_id, apps)
    manifest = {
        "format": bundles.FORMAT, "id": module_id, "version": version, "title": modules.TITLES.get(module_id, module_id),
        "requires": list(modules.REQUIRED), "apps": apps, "stylesheet": sheet, "templates": templates,
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())},
    }
    manifest_bytes = json.dumps(manifest, indent=1, sort_keys=True).encode()
    signature = base64.b64encode(signing_key(key_path).sign(manifest_bytes).signature)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{module_id}-{version}{bundles.EXTENSION}"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", manifest_bytes)
        zf.writestr("manifest.sig", signature)
        for name, data in sorted(files.items()):
            zf.writestr(name, data)
    target.write_bytes(buffer.getvalue())
    return target, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("module")
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "modules")
    parser.add_argument("--key", default=str(ROOT / ".secrets" / "module-signing.key"))
    parser.add_argument("--version", help="instead of VERSION (tests)")
    parser.add_argument("--no-css", action="store_true")
    args = parser.parse_args()
    target, manifest = build(args.module, args.out, args.key, css=not args.no_css, version=args.version)
    print(f"{target} ({len(manifest['files'])} files, {target.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
