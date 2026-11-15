"""Names a school gives its fields, choices, masks, tabs and sections, in several languages.

Every name has a base, the text it was typed in first, and may have its own name per language:
    field.label + field.labels = {"en": "First name"}
    option {"value": "Weiblich", "label": "", "labels": {"en": "Female"}}   (value is what records store)
    mask.name + mask.names, tab/section {"title": …, "titles": {…}}, field item {"label": …, "labels": {…}}
A language without its own name shows the base. Renaming asks whether the new name is for the
language on screen only ("this": it gets its own name) or for all ("all": the base changes and the
names for single languages go), see static/js/names.js.
"""

from django.conf import settings
from django.utils import translation

THIS, ALL = "this", "all"


def languages():
    return [code for code, _ in settings.LANGUAGES]


def current():
    language = translation.get_language() or settings.LANGUAGE_CODE
    return language.lower()


def pick(base, names, language=None):
    """The name to show: the language's own, else the base."""
    if names:
        own = names.get(language or current())
        if own:
            return own
    return base or ""


def clean_names(names, limit=200):
    """Only known languages and non-empty texts, as stored."""
    known = set(languages())
    if not isinstance(names, dict):
        return {}
    return {code: str(text).strip()[:limit] for code, text in names.items() if code in known and str(text or "").strip()}


def scope_for(request, has_names):
    """What a rename sent without the dialog's answer means, the same way the dialog decides."""
    sent = request.POST.get("name_scope", "") if request is not None else ""
    if sent in (THIS, ALL):
        return sent
    if request is not None and single_language(request):
        return quiet_scope(has_names)
    remembered = getattr(getattr(request, "user", None), "name_scope", "")
    if remembered in (THIS, ALL):
        return remembered
    return quiet_scope(has_names)


def quiet_scope(has_names):
    """Without asking: in German a name nobody translated changes for all languages, otherwise only for this one,
    so the other languages keep their names."""
    return ALL if current() == settings.LANGUAGE_CODE and not has_names else THIS


def single_language(request):
    """When this database allows a single language, nobody is asked: the other languages are not shown."""
    from core.views_language import allowed_languages

    return len(allowed_languages(request)) == 1


def rename(base, names, value, scope, language=None):
    """Return (base, names) after renaming to value. An unchanged name changes nothing."""
    names = dict(names or {})
    value = (value or "").strip()
    language = language or current()
    if not value or value == pick(base, names, language):
        return base, names
    if scope == THIS:
        if value == base:
            names.pop(language, None)
        else:
            names[language] = value
        return base, names
    return value, {}


# Options of choice fields. "value" is stored in records and never changes; what people see is
# label/labels, or the value itself while nobody renamed it.

def option_label(option, language=None):
    return pick(option.get("label") or option.get("value", ""), option.get("labels"), language)


def option_base(option):
    return option.get("label") or option.get("value", "")


def rename_option(option, value, scope, language=None):
    option = dict(option)
    base, names = rename(option_base(option), option.get("labels"), value, scope, language)
    if base == option.get("value"):
        option.pop("label", None)
    else:
        option["label"] = base
    if names:
        option["labels"] = names
    else:
        option.pop("labels", None)
    return option


def has_option_names(options):
    return any(o.get("labels") for o in options or [])


# Mask trees

def localize_tree(tree, language=None):
    """A copy of a mask tree with titles and labels in the given language, for showing it."""
    out = {"tabs": []}
    for tab in tree.get("tabs", []):
        sections = []
        for section in tab.get("sections", []):
            items = []
            for item in section.get("items", []):
                item = dict(item)
                if item.get("labels"):
                    item["label"] = pick(item.get("label", ""), item["labels"], language)
                items.append(item)
            sections.append({**section, "title": pick(section.get("title", ""), section.get("titles"), language), "items": items})
        out["tabs"].append({**tab, "title": pick(tab.get("title", ""), tab.get("titles"), language), "sections": sections})
    return out
