"""What optional modules add to the platform, collected when Django starts.

An optional module's AppConfig.ready() calls the functions below. The platform then asks this
registry instead of importing the module's apps, so it runs the same with or without them
(schule/modules.py, docs/modules.md).

Simple lists and tables of the platform are extended in place, so their readers stay as they were:
the menu (core.context.NAV), the search rights (core.context.SEARCHERS), capabilities and role
summaries (core.permissions), and record blocks (masks.registry.BLOCKS, people.blocks.BLOCK_VIEWS).
Everything that needs code goes through a hook:

    home(request)                      -> a response for users without person.view, or None
    search(user, terms)                -> extra search results (dicts with title, sub, url, icon)
    media_access(user, name)           -> True if this user may open an uploaded file the platform would refuse
    private_media                      prefixes of uploaded files that only the module's own views serve
    kept_by(person)                    -> why a person may not go to the recycle bin, or ""
    class_moved(person, old, new, when)
    staff_sections                     {Person flag: callable returning the section's staff model}
    person_badges                      template names shown beside a person's name
    print_sources                      {source: object with load_records, field_catalog, list_columns, sample_record, may_print}
    sync                               object with the optional methods of sync.scope.ModuleScope
    nightly()                          run by the nightly job, e.g. to empty a recycle bin
    demo                               object for seed_demo: accounts, reset(), reset_after(), wanted(), fill(), top_up(), link(user, role)
    tour                               steps for the guided tour (core/tour.py): [{"anchor", "title", "text", "capability", "after"}]
"""
import contextvars
import sys
from collections import defaultdict

HOOKS = ("home", "search", "media_access", "private_media", "kept_by", "class_moved", "staff_sections",
         "person_badges", "print_sources", "sync", "nightly", "demo", "tour")
_hooks = defaultdict(list)  # name -> [(module, value)]

# What belongs to which module, so a database that does not use a module does not see it (active()).
NAV_MODULES = {}         # menu section title -> module
CAPABILITY_MODULES = {}  # capability -> module
BLOCK_MODULES = {}       # mask block -> module
ROLES = []               # the modules' roles as (value, label, module), beside the platform's (core.models.role_choices)
ROLE_SUMMARIES = []      # (value, summary, module) for the user page


def _caller_module():
    """The module of the app whose register() calls in (daz.module -> premium)."""
    from schule import modules as catalog

    return catalog.module_of(sys._getframe(2).f_globals.get("__name__", "").split(".")[0])


def contribute(**hooks):
    module = _caller_module()
    for name, value in hooks.items():
        if name not in HOOKS:
            raise TypeError(f"Unknown module hook: {name}")
        _hooks[name].append((module, value))


def hooks(name):
    """The hooks of the modules this database uses."""
    using = active()
    return [value for module, value in _hooks[name] if module is None or module in using]


def merged(name):
    """Hooks that are dictionaries, merged into one."""
    result = {}
    for part in hooks(name):
        result.update(part)
    return result


def add_nav_section(title, items, before=()):
    """A menu section, placed before the first present section titled as in `before` (or at the end)."""
    from .context import NAV

    titles = [str(t) for t, _ in NAV]
    place = next((titles.index(str(t)) for t in before if str(t) in titles), len(NAV))
    NAV.insert(place, (title, items))
    NAV_MODULES[str(title)] = _caller_module()


def add_capabilities(capabilities, searchers=(), roles=()):
    """{capability: roles}, and the module's roles as [(value, label, summary)]. Searchers are capabilities that open the search."""
    from .context import SEARCHERS
    from .permissions import CAPABILITIES

    module = _caller_module()
    for capability, granted in capabilities.items():
        CAPABILITIES.setdefault(capability, set()).update(granted)
        CAPABILITY_MODULES[capability] = module
    SEARCHERS.extend(s for s in searchers if s not in SEARCHERS)
    for value, label, summary in roles:
        ROLES.append((value, label, module))
        ROLE_SUMMARIES.append((value, summary, module))


def add_area(key, title, section, read=(), edit=(), extras=(), hint=""):
    """An area of the role editor (core/rights.py) for the module's own capabilities: Kein Zugriff / Lesen /
    Bearbeiten, and switches as [(key, title, hint, capabilities)]. Capabilities no area names still get a
    switch of their own."""
    from . import rights

    rights.AREAS.append(rights.Area(key, title, section, hint=hint, read=tuple(read), edit=tuple(edit), module=_caller_module(),
                                    extras=[rights.Extra(k, t, h, tuple(c)) for k, t, h, c in extras]))


def add_capability_check(capability, check):
    """A capability that also holds when check(user) is true, whatever the role."""
    from .permissions import CAPABILITY_CHECKS

    CAPABILITY_CHECKS[capability] = check


def add_block(name, label, icon, view):
    """A block for record masks: its name in mask trees, its label and icon in the designer, and its view."""
    from masks.registry import BLOCKS
    from people.blocks import BLOCK_VIEWS

    BLOCKS[name] = (label, icon)
    BLOCK_VIEWS[name] = view
    BLOCK_MODULES[name] = _caller_module()


def block_active(name):
    module = BLOCK_MODULES.get(name)
    return module is None or module in active()


# ------------------------------------------------------------ the modules a database uses

_request_cache = contextvars.ContextVar("active_modules", default=None)


def active():
    """The loaded modules this database uses: SchoolSettings.modules picks the optional ones (set when the
    database is made, empty list for none); None, as in every database made before, means all loaded ones.
    Cached for one request (ActiveModulesMiddleware); outside a request it is read each time."""
    cache = _request_cache.get()
    if cache is not None and "value" in cache:
        return cache["value"]
    from django.db import DatabaseError

    from schule import modules as catalog

    loaded = set(catalog.selected())
    try:
        from .models import SchoolSettings

        row = SchoolSettings.objects.filter(pk=1).values_list("modules", "modules_off").first()
    except DatabaseError:  # before the first migration
        row = None
    chosen, off = row if row else (None, [])
    value = set(loaded if chosen is None else (set(catalog.REQUIRED) | set(chosen)) & loaded)
    if "schulverwaltung" in (off or []):
        # Only the platform and modules without code of their own (templates only) work without it.
        value = {name for name in value if name == "platform" or not catalog.MODULES.get(name)}
    value = frozenset(value)
    if cache is not None:
        cache["value"] = value
    return value


def start_request():
    return _request_cache.set({})


def end_request(token):
    _request_cache.reset(token)


def optional_modules():
    """[(id, title)] of the optional modules this installation could give a new database."""
    from schule import modules as catalog

    return [(name, catalog.TITLES.get(name, name)) for name in catalog.selected() if name not in catalog.REQUIRED]


# ------------------------------------------------------------ modules of this process and of a database


def loaded_apps():
    """The labels of every app loaded in this process, Django's own included."""
    from django.apps import apps

    return {config.label for config in apps.get_app_configs()}


def manifest():
    """{module: version} of the modules loaded here, for backups and the sync handshake."""
    from django.conf import settings

    from schule import modules as catalog

    return {name: settings.DATAFLUSS_VERSION for name in catalog.selected()}


def titles(names):
    from schule import modules as catalog

    return ", ".join(catalog.TITLES.get(name, name) for name in sorted(names))


def missing_with_data():
    """Modules this database has data of that are not loaded here, e.g. a premium database opened
    without the premium module. Their tables stay untouched; empty ones are ignored."""
    from django.db import connection
    from django.db.migrations.recorder import MigrationRecorder

    from schule import bundles
    from schule import modules as catalog

    if not MigrationRecorder(connection).has_table():
        return set()
    loaded = loaded_apps()
    foreign = set(MigrationRecorder.Migration.objects.values_list("app", flat=True).distinct()) - loaded
    if not foreign:
        return set()
    missing = set()
    with connection.cursor() as cursor:
        tables = connection.introspection.table_names(cursor)
        for app in sorted(foreign):
            for table in (t for t in tables if t.startswith(f"{app}_")):
                cursor.execute(f"SELECT 1 FROM {connection.ops.quote_name(table)} LIMIT 1")
                if cursor.fetchone():
                    # A module not loaded here is named by an older bundle of it, e.g. after an update of the app.
                    missing.add(catalog.module_of(app) or bundles.owner_of(app) or app)
                    break
    return missing


def missing_message(names):
    from django.utils.translation import gettext as _

    return _("Diese Datenbank enthält Daten des Moduls %(names)s, das in dieser DataFluss-Version fehlt. "
             "Sie bleibt unverändert und lässt sich mit einer DataFluss-Version mit diesem Modul öffnen.") % {"names": titles(names)}
