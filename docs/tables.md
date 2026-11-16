# Own tables

Own tables keep data DataFluss does not know out of the box, such as library books, room bookings,
events or an inventory. You design the tables, their fields, record pages and printouts yourself, and
they work like the built-in lists: filters, saved views, inline editing, search, printing, import and
export, sync with a server and backups.

Designing tables needs the IT role. Who may read or edit the records is set for each table.

## Creating a table

**Verwaltung → Einstellungen → Tabellen → Neue Tabelle.** The new table starts with one field, "Name".
**Zugriff** sets every other role at once: only IT, everyone reads, or everyone edits; the Rechte tab sets
each role on its own later. Its settings have four tabs:

- **Felder:** add fields, drag them into order, open one to edit it. While a field's name is typed, the
  type is guessed from it ("E-Mail", "Geburtsdatum", "Preis", "Bemerkung", "Bezahlt?"); a name like another
  table's ("Buch" for "Bücher") or like pupils, staff or devices proposes a link to it.
- **Datensatzseite:** arrange the record page in the mask designer (see below).
- **Rechte:** for every role: no access, read or edit. IT always has full access.
- **Allgemein:** name, description, icon, whether it shows in the sidebar, and which field gives a
  record its title.

A table with records can only be archived, not deleted. An archived table can still be read but not
changed, and it leaves the sidebar.

Instead of starting empty, **Vorlage hinzufügen …** creates ready-made tables (see *Templates*).

### A table from a file

**Aus Excel- oder CSV-Datei** on the same page reads a file and lists its columns as fields, each with a
type guessed from its values: numbers with their decimal places and unit ("12,50 €"), dates, date and
time, yes/no, e-mail, phone, link, long text, and a choice when few values repeat. Postcodes and other
numbers with leading zeros stay text. The title column is guessed too (a column named like "Name" or
"Bezeichnung", else the first one whose values differ). After checking, one click creates the table and
imports every row; the import can be taken back under Import & Export within 24 hours.

## Field types

| Type | Notes |
|---|---|
| Text, Text (mehrzeilig) | |
| Zahl | Decimal places (0–6), a unit such as € or kg, minimum and maximum |
| Ja/Nein | |
| Auswahl, Mehrfachauswahl | Options can be renamed and translated without changing stored records |
| Datum, Datum und Uhrzeit | |
| E-Mail, Telefon, Link | Checked on entry |
| Geheim (verschlüsselt) | Encrypted, shown only to roles with password rights, never in plain text in the history |
| Verknüpfung | Points to records of another table, or to pupils, staff or devices (see *Links*) |
| Datei, Bild | Uploads; an image field shows a preview and prints as the record's photo |
| Berechnung | A formula (see *Formulas*) |

Fields can be required or unique. A field can change to a compatible type (text ↔ multi-line text,
choice → text, number → text, …). Values are converted. For anything else, create a new field.
Archiving a field hides it and keeps its values.

## Record pages

**Neuer Datensatz** opens the new record with the cursor in its first field. Every field is saved when it
is left. The bar above offers **Weiterer Datensatz** for entering several in a row, and **Verwerfen**,
which removes a record that was never filled in (one with values goes to the bin). Records can be put in
the bin from their page or from the list; the bin brings back one or all.

Without any design, a record page shows all fields. **Datensatzseite → Im Maskendesigner gestalten**
opens the same designer as for pupils: tabs, sections, widths, notes, conditions ("show if") and the
history block. Fields a page does not place still appear at the bottom under "Weitere Felder", so a new
field is never invisible.

## Links

A link field points to records of another own table, or to pupils, staff or devices. It can allow one
target or several, and it can have a name for the other side, such as "Ausleihen" for loans pointing to a
book.

- On the record page, a search box offers the targets you may see. Chips show what is linked.
- Records, pupils, staff and devices show a section **Verknüpft in eigenen Tabellen** with everything
  that links to them, under the name for the other side.
- A link to something you may not see shows **nicht sichtbar** instead of a name.

## Formulas

A formula computes a value from other fields. Values are computed whenever they are shown and never
stored, so they are always current and never conflict between computers. The field dialog checks a
formula as you type, shows the result type and the values of the first records, and points to mistakes.
Ready-made computations ("Anzahl verknüpfter Datensätze", "Tage zwischen zwei Daten", "Alter aus
Geburtsdatum", …) fill in a formula you can change.

```
{Preis} * {Menge}
{Vorname} & " " & {Nachname}
if({Rückgabe} = null, days({Ausgabe}, today()), 0)
count({Ausleihen})
sum({Positionen}.{Betrag})
```

- **Fields** are written in braces with their name, in any language the field has a name in. Renaming a
  field never breaks a formula.
- **Linked records:** `{Buch}.{Preis}` gives the price of the linked book(s); records of other tables
  that link here are written with the name for the other side, `{Ausleihen}`, and their fields the same
  way: `sum({Gegenstände}.{Wert})` for a room adds up the value of the items in it.
- **Values:** numbers (`12.5`), text in quotes (`"Text"`), `true`, `false`, `null`.
- **Operators:** `+ - * /`, `&` joins text, `= != < <= > >=`, `and`, `or`, `not`, parentheses.
  A date plus a number is a date that many days later; a date minus a date is the number of days.

| Function | |
|---|---|
| `if(condition, then, else)` | |
| `isempty(x)`, `coalesce(a, b, …)` | Empty test; the first non-empty value |
| `round(x, places)`, `floor(x)`, `ceil(x)`, `abs(x)`, `min(…)`, `max(…)` | |
| `len(t)`, `upper(t)`, `lower(t)`, `trim(t)`, `concat(…)`, `contains(t, part)`, `format(x)` | |
| `today()`, `now()`, `year(d)`, `month(d)`, `day(d)`, `days(from, to)`, `age(born)` | |
| `count(link)`, `sum(link.field)`, `avg(link.field)`, `first(link)`, `join(link, separator)` | Over linked records |

A formula reads other tables only as far as the person looking may; otherwise it shows "nicht sichtbar".
Mistakes found while computing show as **#Fehler**, with the reason as a tooltip. Secret fields cannot be
used, a formula cannot depend on itself, and length, nesting, steps and linked records have limits.
A formula of the record's own fields (without links) may give records their title.

## Import and export

**Import & Export → Importieren** offers the tables you may edit (or **Importieren** in a table's list).
Columns are matched to fields by name and can be changed. Numbers may carry the field's unit
("12,50 €") and German or English separators ("1.234,5", "1,234.5"). Existing records are recognised by a unique
field or the title field, or every row becomes a new record. Link columns name their targets: records by
title, people by "First Last", "Last, First" or e-mail, devices by number, several separated by `;`.
The preview lists new, changed and unchanged rows and what could not be found. An import can be taken
back within 24 hours, unless its records were changed since.

**Excel** and **CSV** in a table's list export what the list shows, as it is shown.

## Printing

Every table is a print source: **Druckvorlagen → Neue Vorlage → Tabelle: …**. Fields print as shown on
screen, the first image field as the photo. The list element ("Liste über Verknüpfung") prints the
records of a link field or of a table linking here, with the columns you choose. Print from the table's
list (selected or all records), from a record page or from the side panel.

## Rights and computers connected to a server

A computer connected to a server receives only the tables its user's role may read, with their records,
links, files and record pages. Edits are checked by the server against the table's rights and the
fields' types. When a role loses its right, the records leave its computers. Secret values stay off
computers whose role has no password rights.

## Templates

A template creates tables, fields, record pages, printouts, saved views and, if wanted, sample records in
one step: **Tabellen → Vorlage hinzufügen …**, or when creating a new database in the app. Applying a
template again, or a newer version of it, only adds what is missing; nothing is deleted, and what you
changed is kept and named.

DataFluss comes with **Bibliothek** (books and loans to pupils; needs Schulverwaltung), **Inventar** (rooms
and the items in them, with value, condition, labels and room lists) and **Termine** (events with place,
kind and a notice to print). Modules can bring further templates.

### Writing a template

A template is one JSON file. A module can consist of templates only: its `datafluss-module.json` lists
them (`"apps": [], "templates": ["vorlagen/inventar.json"]`), and `scripts/build_module.py` signs it into
a bundle like any other module.

```json
{
  "format": 1, "id": "inventar", "version": 1,
  "title": "Inventar", "titles": {"en": "Inventory"}, "description": "…", "requires": [],
  "tables": [
    {"key": "geraete", "name": "Geräte", "icon": "laptop", "title_field": "f_bezeichnung",
     "rights": {"sekretariat": "edit", "schulleitung": "read"},
     "fields": [
       {"key": "f_bezeichnung", "label": "Bezeichnung", "type": "text", "required": true},
       {"key": "f_wert", "label": "Wert", "type": "number", "config": {"decimals": 2, "unit": "€"}},
       {"key": "f_raum", "label": "Raum", "type": "link", "config": {"target": "record", "table": "raeume", "reverse": "Geräte"}},
       {"key": "f_alter", "label": "Alter in Tagen", "type": "formula", "config": {"expression": "days({f_gekauft}, today())"}}
     ]}
  ],
  "masks": [{"table": "geraete", "name": "Gerät", "tree": {"tabs": []}}],
  "prints": [{"table": "geraete", "name": "Etikett", "tree": {"page": {"preset": "label-3474"}, "rows": []}}],
  "views": [{"table": "geraete", "name": "Teuer", "config": {"sort": [{"column": "f_wert", "dir": "desc"}]}}],
  "demo": {"geraete": [{"f_bezeichnung": "Beamer", "f_wert": "450"}]}
}
```

- Keys: tables `[a-z0-9_]{1,20}`, fields `f_…`; they never change once applied.
- `requires: ["schulverwaltung"]` for templates that link to pupils, staff or devices.
- Formulas are written with field keys (`{f_wert}`, `{f_raum}.{f_name}`, `{@geraete.f_raum}` for the
  records of another table linking here).
- In demo records, a link names its target record by title.
- A newer version raises `version`; it may add tables, fields and the rest, never remove them.
- Table keys of a template should start with a short prefix of its own (`inv_raeume`), so they never meet
  a table someone made by hand. A formula may use formulas of other tables of the template, in any order.

## Databases without Schulverwaltung

When creating a database in the app, the card **Leer** starts without pupils, staff and devices: own
tables only. Its start page lists the tables, and printing, import and links offer tables only. Templates
that need Schulverwaltung, like the library, are not offered there; Inventar and Termine are.
