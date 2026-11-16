"""What a device may hold and change, decided by its user's role (core.permissions).

A device gets its user's sections plus everything those records point to, so every foreign key
resolves on the device. Accounts other than its own user's arrive as stubs that cannot sign in.
"""
from django.db.models import Q

from core.models import User

from . import codec


def _models():
    from core.models import SavedView, UserRole
    from devices import models as devices
    from masks import models as masks
    from people import models as people
    from prints import models as prints
    from transfer import models as transfer
    from vault import models as vault

    return {
        "config": [masks.FieldDefinition, masks.Mask, masks.MaskVersion, people.SchoolYear, people.ClassGroup,
                   prints.PrintTemplate, prints.PrintTemplateVersion, vault.Platform, UserRole],
        "saved_view": SavedView,
        "import": [transfer.ImportPreset],
        "people": [people.Person, people.Enrollment, people.Guardian, people.Guardianship, people.Card, people.Comment, people.Attachment],
        "devices": [devices.AssetCategory, devices.Place, devices.Asset, devices.Assignment, devices.SupportCase,
                    devices.ReturnCampaign, devices.CampaignItem],
        "vault": [vault.Credential],
        "person": people.Person,
    }


class ModuleScope:
    """What a module adds to a device's scope (core/modules.py "sync"). Every method may be left out."""

    def read(self, scope, add):
        """Adds the records of the module this user's device holds: add(model, queryset=None)."""

    def may_write(self, scope, model, pk, label):
        """True or False for the module's own models, None for all others."""
        return None

    def allows(self, scope, model, pk, when):
        """After a push: whether the record, as the push left it, is one this user may change."""
        return True

    def knows_people(self, user):
        """Whether the user sees whole person records through this module."""
        return False

    def edits_people(self, user):
        """Whether the user may change person records, their comments and attachments through this module."""
        return False

    def change_day(self, stamps):
        """The day a pushed change was made, for rules that depend on it, or None."""
        return None

    def hide(self, scope, model):
        """Field maskers for the module's own models ({field: function(value)}), as Scope.hide."""
        return {}

    def ignored(self, scope, model):
        """Keys of the module's own models that this device never sends, as Scope.ignored_keys."""
        return set()


def module_scopes():
    from core import modules

    return modules.hooks("sync")


def _secret_person_keys():
    from masks.models import FieldDefinition

    return set(FieldDefinition.objects.filter(type="secret").values_list("key", flat=True))


# What a teacher's device holds of a person: enough to recognise and grade a pupil, nothing about home or family.
TEACHER_SEES = {"kind", "first_name", "last_name", "family_name_first", "gender", "birth_date", "current_class", "school_type",
                "grade_level", "dyslexia", "photo", "archived", "deleted_at", "entry_date", "exit_date", "abbreviation", "category",
                "legacy_photo_path"}


def _blank(field):
    if field.null:
        return lambda value: None
    if isinstance(field.default, (bool, int)) and not callable(field.default):
        default = field.default
        return lambda value: default
    if callable(field.default):
        return lambda value: field.default()
    return lambda value: ""


def _teacher_view_of_person():
    from people.models import Person

    return {f.name: _blank(f) for f in Person._meta.concrete_fields
            if not f.primary_key and f.name not in TEACHER_SEES and f.name not in ("created_at", "updated_at")}


class Scope:
    def __init__(self, user):
        self.user = user
        self.models = _models()
        self.modules = module_scopes()
        self.vault = user.can("vault.view")
        self._records = None
        self.when = None  # the day the change being checked was made on the device (ModuleScope.change_day)

    def change_day(self, stamps):
        return next((day for module in self.modules if (day := module.change_day(stamps)) is not None), None)

    def allows(self, model, pk, when=None):
        """After a push: whether the record, as the push left it, is one this user may change (the modules' rules)."""
        return all(module.allows(self, model, pk, when) for module in self.modules)

    # ------------------------------------------------------------ reading

    def _base(self):
        m, can = self.models, self.user.can
        sets = {}

        def add(model, queryset=None):
            queryset = model._base_manager.all() if queryset is None else queryset
            sets.setdefault(model, set()).update(queryset.values_list("pk", flat=True))

        for model in m["config"]:
            if model._meta.label_lower == "masks.mask":  # a table's masks come with the table (tables/sync_scope.py)
                add(model, model._base_manager.filter(table=None))
            elif model._meta.label_lower == "masks.maskversion":
                add(model, model._base_manager.filter(mask__table=None))
            else:
                add(model)
        add(m["saved_view"], m["saved_view"]._base_manager.filter(Q(owner=self.user) | Q(owner=None)))
        if can("data.import"):
            for model in m["import"]:
                add(model)
        if can("person.view"):
            for model in m["people"]:
                add(model)
        if can("device.view"):
            for model in m["devices"]:
                add(model)
        if self.vault:
            for model in m["vault"]:
                add(model)
        for module in self.modules:
            module.read(self, add)
        return sets

    def records(self):
        """{model: set of primary keys}, closed over foreign keys."""
        if self._records is not None:
            return self._records
        sets = self._base()
        synced = set(codec.synced_models())
        new = {model: set(pks) for model, pks in sets.items()}
        while new:
            found = {}
            for model, pks in new.items():
                for field in model._meta.concrete_fields:
                    target = field.related_model if field.is_relation else None
                    if target not in synced or not pks:
                        continue
                    ids = set(model._base_manager.filter(pk__in=pks).exclude(**{field.attname: None}).values_list(field.attname, flat=True))
                    missing = ids - sets.get(target, set())
                    if missing:
                        found.setdefault(target, set()).update(missing)
            for model, pks in found.items():
                sets.setdefault(model, set()).update(pks)
            new = found
        self._records = sets
        return sets

    def holds(self, model, pk):
        return pk in self.records().get(model, ())

    def users(self):
        """The accounts the device's records point to, plus its own."""
        ids = {self.user.pk}
        for model, pks in self.records().items():
            for field in model._meta.concrete_fields:
                if field.is_relation and field.related_model is User and pks:
                    ids.update(i for i in model._base_manager.filter(pk__in=pks).values_list(field.attname, flat=True) if i)
        return ids

    def hide(self, model):
        """Field maskers for what this user may not see on its device."""
        maskers = {}
        for module in self.modules:
            maskers.update(module.hide(self, model))
        return {**self._hide(model), **maskers}

    def _hide(self, model):
        label = codec.entity_of(model)
        if label == "people.person" and not self.knows_people:
            return _teacher_view_of_person()
        if self.vault:
            return {}
        if label == "people.person":
            secret = _secret_person_keys()
            return {"custom": lambda value: {k: v for k, v in (value or {}).items() if k not in secret}}
        if label == "devices.asset":
            return {"product_key_enc": lambda value: ""}
        return {}

    # ------------------------------------------------------------ writing

    def may_write(self, model, pk, keys):
        """Whether the device's user may make this change. Scope is checked after the whole push."""
        can, label = self.user.can, codec.entity_of(model)
        app = label.split(".")[0]
        if label == "core.savedview":
            from core.models import SavedView

            owner = SavedView._base_manager.filter(pk=pk).values_list("owner_id", flat=True).first()
            return owner in (None, self.user.pk)
        if label == "core.userrole":
            return can("admin.users")
        if label == "people.person":
            return can("person.edit") or self.edits_people
        if label in ("people.schoolyear", "people.classgroup"):
            return can("class.manage")
        if label in ("people.comment", "people.attachment"):
            return can("person.edit") or self.edits_people
        if app == "people":
            return can("person.edit")
        if app == "devices":
            return can("device.edit")
        if label == "vault.credential":
            return can("vault.edit")
        if label == "vault.platform":
            return can("admin.platforms")
        if label == "masks.fielddefinition":
            return can("admin.fields")
        if app == "masks":
            return can("admin.masks")
        if app == "prints":
            return can("print.design")
        if app == "transfer":
            return can("data.import")
        for module in self.modules:
            allowed = module.may_write(self, model, pk, label)
            if allowed is not None:
                return allowed
        return False

    @property
    def knows_people(self):
        """Whether the user may see whole person records, not just what teaching needs."""
        return self.user.can("person.view") or any(module.knows_people(self.user) for module in self.modules)

    @property
    def edits_people(self):
        return any(module.edits_people(self.user) for module in self.modules)

    def ignored_keys(self, model):
        """Keys a device without vault access never sends, so its pushes leave them alone."""
        keys = set()
        for module in self.modules:
            keys |= module.ignored(self, model)
        return self._ignored_keys(model) | keys

    def _ignored_keys(self, model):
        label = codec.entity_of(model)
        if label == "people.person" and not self.knows_people:
            return set(_teacher_view_of_person())
        if self.vault:
            return set()
        if label == "people.person":
            return {f"custom.{k}" for k in _secret_person_keys()}
        if label == "devices.asset":
            return {"product_key_enc"}
        return set()
