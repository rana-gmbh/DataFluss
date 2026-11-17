# PyInstaller spec for the desktop client's sidecar. Build with client/build-sidecar.sh.
import json
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent.parent
# collect_submodules imports the project's packages, so they must be on the path while the spec runs.
sys.path.insert(0, str(ROOT))
os.environ["PYTHONPATH"] = str(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "schule.settings")
os.environ.setdefault("SECRET_KEY", "build")
from schule import modules  # noqa: E402

# The apps of the modules this build carries (schule/modules.py; DATAFLUSS_MODULES chooses, as at run time).
APPS = ["schule", *modules.apps()]
print(f"sidecar.spec: modules {', '.join(modules.selected())}")

hidden = []
for app in APPS:
    found = collect_submodules(app, filter=lambda name: ".tests" not in name)
    assert found, f"No modules found for {app}"
    hidden += found  # Django finds migrations and commands by name, so PyInstaller cannot see them.
# A module loaded from a bundle (schule/bundles.py) brings only its own code. Whatever of Python's standard
# library and of Django it imports must already be here, whether or not this build carries the module.
STDLIB_LEFT_OUT = {"tkinter", "turtle", "turtledemo", "idlelib", "test", "lib2to3", "ensurepip", "venv", "pydoc_data", "curses",
                   "readline", "antigravity", "this", "msilib", "winsound", "winreg", "msvcrt", "nt", "_winapi", "posix", "pwd",
                   "grp", "termios", "tty", "pty", "fcntl", "resource", "syslog", "nis", "spwd", "crypt", "ossaudiodev", "sndhdr",
                   "audioop", "chunk", "cgi", "cgitb", "imghdr", "mailcap", "nntplib", "pipes", "telnetlib", "uu", "xdrlib"}
hidden += [name for name in sorted(sys.stdlib_module_names) if not name.startswith("_") and name not in STDLIB_LEFT_OUT]
hidden += collect_submodules("django", filter=lambda name: ".tests" not in name and not name.startswith((
    "django.contrib.gis", "django.contrib.postgres", "django.db.backends.postgresql", "django.db.backends.mysql",
    "django.db.backends.oracle", "django.contrib.admin", "django.contrib.admindocs", "django.contrib.flatpages",
    "django.contrib.redirects", "django.contrib.sitemaps", "django.contrib.sites", "django.contrib.syndication")))
hidden += collect_submodules("django.contrib.auth") + collect_submodules("django.contrib.sessions")
hidden += collect_submodules("django.contrib.messages") + collect_submodules("django.contrib.contenttypes")
hidden += collect_submodules("django.contrib.staticfiles") + collect_submodules("whitenoise")
hidden += collect_submodules("mozilla_django_oidc") + collect_submodules("argon2") + collect_submodules("truststore")  # Django loads the hasher by name.

datas = [
    (str(ROOT / "templates"), "templates"),
    (str(ROOT / "staticfiles"), "staticfiles"),
    (str(ROOT / "locale"), "locale"),  # the compiled translations (.mo files are committed)
    (str(ROOT / "prints" / "fonts"), "prints/fonts"),
    (str(ROOT / "VERSION"), "."),  # settings.DATAFLUSS_VERSION
]
# The optional modules this build carries, for the start screen's new-database wizard (main.rs available_modules).
_modules_json = Path(SPECPATH).parent / "build" / "modules.json"
_modules_json.parent.mkdir(parents=True, exist_ok=True)
def _template_list():
    """The module templates this build carries (tables/blueprints.py), for the wizard's choice."""
    found = []
    for path in [*sorted((ROOT / "tables" / "library").glob("*.json")), *(Path(p) for m in modules.selected() for p in modules.TEMPLATE_FILES.get(m, []))]:
        data = json.loads(path.read_text(encoding="utf-8"))
        found.append({**{key: data.get(key) for key in ("id", "title", "titles", "description", "descriptions", "requires", "database", "order", "listed")},
                      "has_demo": bool(data.get("demo"))})
    return found


_modules_json.write_text(json.dumps({"optional": [{"id": name, "title": modules.TITLES.get(name, name)}
                                                  for name in modules.selected() if name not in modules.REQUIRED],
                                     "templates": _template_list()}))
datas.append((str(_modules_json), "."))
datas.append((str(ROOT / "tables" / "library"), "tables/library"))  # the platform's module templates
# Templates inside an app (a module's own), where Django's app template loader looks for them.
datas += [(str(ROOT / app / "templates"), f"{app}/templates") for app in APPS if (ROOT / app / "templates").is_dir()]
WINDOWS = sys.platform == "win32"
datas.append((str(Path(SPECPATH) / ("fonts.windows.conf" if WINDOWS else "fonts.conf")), "fontconfig"))
datas += collect_data_files("django") + collect_data_files("sqlcipher3")

# WeasyPrint opens these with dlopen, so PyInstaller cannot see them. Their own dependencies follow automatically
# (on Windows from the PATH, so build-sidecar.ps1 puts MSYS2's ucrt64/bin on it).
BREW = Path("/opt/homebrew/opt")
UCRT = Path(os.environ.get("MSYS2_ROOT", "C:/msys64")) / "ucrt64" / "bin"
binaries = [(str(UCRT / dll), ".") for dll in (
    "libgobject-2.0-0.dll",
    "libpango-1.0-0.dll",
    "libpangoft2-1.0-0.dll",
    "libharfbuzz-0.dll",
    "libharfbuzz-subset-0.dll",
    "libfontconfig-1.dll",
)] if WINDOWS else [(str(BREW / lib), ".") for lib in (
    "glib/lib/libgobject-2.0.0.dylib",
    "pango/lib/libpango-1.0.0.dylib",
    "pango/lib/libpangoft2-1.0.0.dylib",
    "harfbuzz/lib/libharfbuzz.0.dylib",
    "harfbuzz/lib/libharfbuzz-subset.0.dylib",
    "fontconfig/lib/libfontconfig.1.dylib",
)]

a = Analysis(
    [str(Path(SPECPATH) / "sidecar.py")],
    pathex=[str(ROOT)],
    hiddenimports=hidden,
    binaries=binaries,
    datas=datas,
    # The client needs neither PostgreSQL nor the server's offsite copy, nor the test tools.
    excludes=["psycopg", "psycopg_binary", "boto3", "botocore", "s3transfer", "pymupdf", "fitz", "tkinter", "gunicorn"],
    noarchive=False,
)
# Pillow ships HarfBuzz, FreeType and libpng under the same install names as the Homebrew builds that
# Pango needs, and only one copy of each can be loaded. Pillow's older HarfBuzz under Homebrew's
# harfbuzz-subset crashed WeasyPrint, so both get Homebrew's newer builds, which stay ABI-compatible.
SHARED = {
    "libharfbuzz.0.dylib": BREW / "harfbuzz/lib/libharfbuzz.0.dylib",
    "libfreetype.6.dylib": BREW / "freetype/lib/libfreetype.6.dylib",
    "libpng16.16.dylib": BREW / "libpng/lib/libpng16.16.dylib",
}
if not WINDOWS:  # Pillow's Windows wheels link these statically, so there is nothing to swap.
    a.binaries = [(dest, str(SHARED[Path(dest).name]) if Path(dest).name in SHARED else src, kind) for dest, src, kind in a.binaries]

pyz = PYZ(a.pure)
exe = EXE(
    # UTF-8 mode on Windows, as on macOS: files the app writes without naming an encoding (CSV exports,
    # demo logins) would otherwise be in the ANSI code page.
    pyz, a.scripts, [("X utf8", None, "OPTION")] if WINDOWS else [],
    exclude_binaries=True,
    name="schule-sidecar",
    console=True,
    # macOS only; Windows builds are signed after bundling.
    codesign_identity=None if WINDOWS else os.environ.get("CODESIGN_IDENTITY"),
    entitlements_file=None if WINDOWS else str(Path(SPECPATH) / "entitlements.plist"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="schule-sidecar")
