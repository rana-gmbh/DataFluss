"""Roles in the words of the people who set them up: areas and switches instead of capabilities.

A role (core.models.UserRole) stores, per area, "none", "read" or "edit", and a list of switched-on extras.
This module turns that into the capabilities the code checks (core/permissions.py has_capability), and
back: the roles that exist today were written as capability sets, and `from_capabilities` finds the areas
and extras that give exactly those sets, so a seeded role behaves as before.

Modules add their areas with core/modules.py add_area; a module capability no area names still gets a
switch of its own (`areas()` adds them), so every capability can be granted.
"""
from dataclasses import dataclass, field

from django.utils.translation import gettext_lazy as _

NONE, READ, EDIT = "none", "read", "edit"
LEVELS = (NONE, READ, EDIT)


@dataclass
class Extra:
    key: str
    title: str
    hint: str = ""
    capabilities: tuple = ()
    needs: str = READ  # the area level it needs ("" for areas without levels)


@dataclass
class Area:
    key: str
    title: str
    section: str
    hint: str = ""
    read: tuple = ()  # capabilities for "read"; "edit" adds `edit`
    edit: tuple = ()
    extras: list = field(default_factory=list)
    module: str = "platform"

    @property
    def has_levels(self):
        return bool(self.read or self.edit)


PEOPLE, IT, PRINT, ADMIN, TABLES = _("Personen"), _("IT"), _("Drucken und Daten"), _("Verwaltung"), _("Tabellen")

PLATFORM_AREAS = [
    Area("home", _("Startseite"), _("Übersicht"), _("Die Startseite mit Zahlen, Aufgaben und der Suche."), read=("home.view",)),
    Area("pupils", _("Schüler"), PEOPLE, _("Liste, Datenblätter und Suche; Bearbeiten heißt anlegen und ändern."),
         read=("pupil.view",), edit=("pupil.edit",), extras=[
             Extra("pupils.archive", _("Archivieren und löschen"), _("Schüler archivieren, in den Papierkorb legen und zurückholen."),
                   ("pupil.archive",)),
             Extra("pupils.classes", _("Klassen und Schuljahreswechsel"), _("Klassen anlegen und ändern, das Schuljahr wechseln."),
                   ("class.manage",)),
         ], module="schulverwaltung"),
    Area("staff", _("Personal"), PEOPLE, _("Liste, Datenblätter und Suche; Bearbeiten heißt anlegen und ändern."),
         read=("staff.view",), edit=("staff.edit",), extras=[
             Extra("staff.archive", _("Archivieren und löschen"), _("Personal archivieren, in den Papierkorb legen und zurückholen."),
                   ("staff.archive",)),
         ], module="schulverwaltung"),
    Area("guardians", _("Eltern"), PEOPLE, _("Erziehungsberechtigte und ihre Kinder."),
         read=("guardian.view",), edit=("guardian.edit",), module="schulverwaltung"),
    Area("devices", _("Geräte und Schäden"), IT, _("Geräte, Ausgaben, Rücknahmen und Schadensfälle."),
         read=("device.view",), edit=("device.edit",), module="schulverwaltung"),
    Area("vault", _("Zugänge"), IT, _("Benutzernamen und Passwörter der Plattformen."),
         read=("vault.view",), edit=("vault.edit",), extras=[
             Extra("vault.reveal", _("Passwörter anzeigen"), _("Ein verborgenes Passwort im Klartext zeigen."), ("vault.reveal",)),
             Extra("vault.export", _("Mit Passwörtern exportieren"), _("Zugänge samt Passwörtern als Datei."), ("vault.export",)),
             Extra("vault.platforms", _("Plattformen verwalten"), _("Plattformen anlegen und ändern."), ("admin.platforms",)),
         ], module="schulverwaltung"),
    Area("print", _("Drucken"), PRINT, _("Berichte, Etiketten und Briefe als PDF."), read=("print.run",), extras=[
        Extra("print.secrets", _("Zugangsblätter drucken"), _("Vorlagen, die Passwörter enthalten."), ("print.secrets",)),
        Extra("print.design", _("Vorlagen gestalten"), _("Druckvorlagen anlegen und ändern."), ("print.design",)),
        Extra("print.code", _("Code-Modus"), _("Vorlagen als Code bearbeiten."), ("print.code",)),
    ]),
    Area("data", _("Import und Export"), PRINT, _("Lesen heißt exportieren, Bearbeiten auch importieren."),
         read=("data.export",), edit=("data.import",)),
    Area("tables", _("Eigene Tabellen"), TABLES, _("Welche Tabelle eine Rolle sieht, steht bei jeder Tabelle."), extras=[
        Extra("tables.design", _("Tabellen gestalten"), _("Tabellen, Felder, Datensatzseiten und Rechte der Tabellen."),
              ("tables.design",), needs=""),
    ]),
    Area("admin", _("Verwaltung"), ADMIN, extras=[
        Extra("admin.fields", _("Felder anlegen und ändern"), _("Eigene Felder für Schüler und Personal."), ("admin.fields",), needs=""),
        Extra("admin.masks", _("Datenblätter gestalten"), _("Masken für Schüler und Personal."), ("admin.masks",), needs=""),
        Extra("admin.users", _("Benutzer und Rollen"), _("Konten anlegen und Rollen festlegen."), ("admin.users",), needs=""),
        Extra("admin.school", _("Schule oder Organisation"), _("Name, Logo und Sprachen."), ("admin.school",), needs=""),
        Extra("admin.update", _("Aktualisierung"), _("Neue Versionen einspielen."), ("admin.update",), needs=""),
        Extra("admin.backup", _("Sicherung"), _("Sicherungen anlegen und zurückspielen."), ("admin.backup",), needs=""),
        Extra("publish.manage", _("Veröffentlichen"), _("Ausgewählte Tabellen als Website veröffentlichen, die jeder lesen kann."),
              ("publish.manage",), needs=""),
        Extra("admin.audit", _("Protokoll"), _("Wer wann was getan hat."), ("admin.audit",), needs=""),
    ]),
]

AREAS = list(PLATFORM_AREAS)  # modules append theirs (core/modules.py add_area)


def areas(in_use=False):
    """Every area, and a switch for each module capability no area names, under the module's own area.
    With in_use, only what this database uses: no people, devices or logins without Schulverwaltung, no
    areas of modules it does not use (the role editor and its summary)."""
    from . import modules
    from .permissions import CAPABILITIES

    found, using = list(AREAS), None
    if in_use:
        try:
            using = set(modules.active()) | {"platform"}
        except Exception:  # noqa: BLE001 - no settings table yet (during migrate)
            using = None
        if using is not None:
            found = [_without_unused(area, using) for area in found if area.module in using]
    named = {cap for area in found for cap in capabilities_of(area)}
    loose = {}
    for capability in CAPABILITIES:
        module = modules.CAPABILITY_MODULES.get(capability)
        if capability in named or module in (None, "platform", "schulverwaltung"):
            continue
        loose.setdefault(module, []).append(Extra(f"cap:{capability}", capability, "", (capability,), needs=""))
    for module, extras in loose.items():
        if not in_use or module in (using or {module}):
            found.append(Area(f"module:{module}", str(module), _("Module"), extras=extras, module=module))
    return found


def _without_unused(area, using):
    """The area without switches whose capabilities belong to a module the database does not use
    (Felder anlegen and Masken need Schulverwaltung, say)."""
    from . import modules

    extras = [e for e in area.extras if all(modules.CAPABILITY_MODULES.get(c, "platform") in using for c in e.capabilities)]
    return area if len(extras) == len(area.extras) else Area(area.key, area.title, area.section, area.hint, area.read, area.edit, extras, area.module)


def capabilities_of(area):
    return {*area.read, *area.edit, *(cap for extra in area.extras for cap in extra.capabilities)}


def capabilities(levels, extras):
    """The capabilities granted by {area: level} and a list of extra keys. An extra "cap:<capability>" is a
    module capability no area named when the role's row was made (`normalize` turns it into its area)."""
    granted = {extra[4:] for extra in extras or [] if extra.startswith("cap:")}
    switched = set(extras or [])
    for area in areas():
        level = (levels or {}).get(area.key, NONE)
        if level in (READ, EDIT):
            granted.update(area.read)
        if level == EDIT:
            granted.update(area.edit)
        for extra in area.extras:
            if extra.key in switched and (not extra.needs or level in (READ, EDIT)):
                granted.update(extra.capabilities)
    return granted


def from_capabilities(granted):
    """({area: level}, [extras]) that give exactly `granted` (for capabilities areas know)."""
    levels, extras = {}, []
    for area in areas():
        if area.has_levels:
            if set(area.read) <= granted and set(area.edit) <= granted and area.edit:
                levels[area.key] = EDIT
            elif set(area.read) <= granted:
                levels[area.key] = READ
            else:
                levels[area.key] = NONE
        for extra in area.extras:
            if set(extra.capabilities) <= granted:
                extras.append(extra.key)
    return levels, extras


def normalize(levels, extras):
    """The same rights in today's areas: switches of single capabilities ("cap:…") become the area a module
    has added for them since. Capabilities no area knows stay as such switches."""
    granted = capabilities(levels, extras)
    new_levels, new_extras = from_capabilities(granted)
    named = capabilities(new_levels, new_extras)
    new_extras += [f"cap:{capability}" for capability in sorted(granted - named)]
    return new_levels, new_extras
