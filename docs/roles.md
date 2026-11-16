# Roles and rights

Every account has exactly one role. A role says which areas the account sees, what it may change there,
and which fields it may only read or not see at all. Roles are set up under
**Verwaltung → Einstellungen → Benutzer und Rollen → Rollen**, by IT or by a role with the right
"Benutzer und Rollen".

## The roles that come with DataFluss

- **IT** may do everything and cannot be changed, so nobody can lock themselves out. The last active IT
  account keeps its role and cannot be locked.
- **Sekretariat**, **Schulleitung** and the roles of modules (DaZ-Lehrkraft, Kindergarten-Fachkraft,
  Lehrkraft) start with the rights they always had and can be changed like any other role.
- A database without Schulverwaltung (the card **Leer** in the app) has no Sekretariat and Schulleitung.
  It starts with **Team** (edits own tables, prints) and **Gast** (reads them). The role editor then only
  shows areas of the modules in use, and the suggestions are Alles lesen and Alles bearbeiten.

## A new role

**Neue Rolle** asks for a name and a starting point: only the start page, a suggestion (Alles lesen,
Hausmeister, Büro) or a copy of an existing role. The role editor opens right away; everything can be
changed later. A role with accounts can only be deleted once its accounts get another role.

## The role editor

- **Allgemein**: name (with translations), description, and the accounts that have the role.
- **Bereiche**, grouped like the sidebar. Each area has **Kein Zugriff**, **Lesen** (see and print) or
  **Bearbeiten** (also create and change). Once an area is open, its switches appear, for example:

  | Area | Switches |
  |---|---|
  | Schüler | Archivieren und löschen · Klassen und Schuljahreswechsel |
  | Personal | Archivieren und löschen |
  | Zugänge | Passwörter anzeigen · Mit Passwörtern exportieren · Plattformen verwalten |
  | Drucken | Zugangsblätter drucken · Vorlagen gestalten · Code-Modus |
  | Import und Export | Lesen exports, Bearbeiten also imports |
  | Unterricht und Noten (module) | Zeugnisse und Einrichtung · Unterricht verwalten · Alle Unterrichte sehen · Zeugnisse drucken · Zeiträume abschließen |

  Schüler, Personal and Eltern are separate areas: a role can, for example, manage staff without seeing
  any pupil.
- **Felder**: for pupils, staff and every own table, each field is **Bearbeiten** (as the area allows),
  **Nur lesen** or **Verborgen**. Fields are grouped like the record page; a search and "Alle nur lesen"
  help with long lists. First and last name, and a table's title field (with the fields its title formula
  uses), can be read-only but never hidden, so every record keeps a name.
- **Eigene Tabellen**: every table with Kein Zugriff, Lesen or Bearbeiten, the same rights as in the
  table's tab "Rechte", and the switch "Tabellen gestalten".
- **Gestalten und Verwalten**: fields, masks, users and roles, school and languages, updates, backups,
  protocol.

The column on the right says in sentences what the role can do and shows the sidebar its accounts get,
while the role is changed. Saving lists the changes and how many accounts they apply to.

A field's page (Felder, or a table's field) has a box **Rechte** with every role: the same rules, seen
from the field.

## What "Verborgen" means

A hidden field is not on record pages or side panels, not in lists or their data, not in the history,
search (an e-mail address that is hidden finds nobody; a table with hidden fields is searched by title),
printouts, exports or the import's columns. A formula that uses it shows **nicht sichtbar** to that role.
Computers connected to a server never receive its value, nor links and files of such a field. When the
rule changes, the computers get the records again, so values leave or arrive.

**Nur lesen** fields are shown but refused on every way of changing them: record page, list, new person,
import, and changes sent from a connected computer.

## Safety

- Only roles with "Benutzer und Rollen" open these pages.
- Without being IT, one can only grant rights one has oneself, give no role that may do more than one's
  own (and never IT), not change one's own role, and not change accounts that may do more.
- Every change is in the protocol.

## For module authors

A module adds its rights to the editor with `modules.add_area(key, title, section, read=[…], edit=[…],
extras=[(key, title, hint, [capabilities])])`. Capabilities no area names still appear as switches of
their own; once a module adds an area for them, existing roles are converted on the next migrate.
