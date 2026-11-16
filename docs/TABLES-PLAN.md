# Own tables: plan for DataFluss 1.2

DataFluss gets a table engine: people design their own tables, fields, record pages, links, computed
fields and printouts, much as in Ninox, without code. Later basic modules are built on it as
**module templates** (a JSON file, no Python). This plan replaces the earlier decision in the school's
old plan that ruled own tables out.

Decided on 2026-11-16:

- **Everything in 1.2:** tables and fields, lists with filters and saved views, record pages in the mask
  designer, link fields, computed fields with a formula language, CSV/Excel import and export,
  printing, module templates, sync and backup.
- **Links to built-in records from the start:** a link field can point to another own table or to a
  pupil, staff member or device.
- **Rights per table and role:** none, read or edit; computers connected to a server receive only the
  tables their user's role may read. Designing tables is a capability of its own (IT by default).
- **Computed fields:** a few ready-made computations *and* a small formula language. The ready-made
  ones are formula presets, so there is one engine.

## 1. What exists and is reused

| Piece | Today | In the table engine |
|---|---|---|
| Field types, `coerce` | `masks/services.py`, bound to Person (`PERSON_COLUMNS`, `person.custom`, secret context `person:…`) | Extracted into a shared field type registry; Person keeps using it unchanged |
| Field definitions | `masks.FieldDefinition`, `entity` = pupil/staff | Own tables get their own `tables.Field` with the same type registry |
| Record pages | `masks.Mask` + `MaskVersion.tree` (tabs → sections → items in a 12-column grid), builder.js | `Mask` gets an optional table; the same designer, new blocks |
| Lists | grid.js on Tabulator 6, client-side filters, `core.SavedView` | grid.js made entity-neutral (edit URL, panel, presets from the config) |
| Sync | every `TrackedModel` syncs; JSON dicts diffed per key; last writer wins per key; scope by hook | New models are TrackedModels; the `tables` app contributes a `sync` hook |
| Backups | every managed model automatically; secrets re-encoded through `_secrets()` | Automatic; `_secrets()` learns secret fields of tables |
| Recycle bin | `RecycledModel`, UI only for Person | Records are RecycledModels; a bin page per table |
| History | `ChangeLog` per model field; a JSON field is logged as one value | ChangeLog splits JSON dicts per key (also improves Person's custom fields) |
| Printing | sources dispatched by if/elif; module sources through `print_sources`; `PrintTemplate.source` max 20 chars with fixed choices | Source `table:<key>` per table; free source field validated by `available_sources()` |
| Import | `transfer/importer.py`, Person only; parsers and preview/commit/undo flow generic | A target protocol: PersonTarget (today's code) and TableTarget |
| Export | `exporters.py` generic; `export_rows` allows pupil/staff | Accepts `table:<key>` |
| Global search | hardcoded queries + `search` hook | The `tables` app contributes a search hook |
| Modules | catalog in `schule/modules.py`, `contribute(...)` hooks, signed bundles with Python apps | Data-only bundles carrying templates; Schulverwaltung becomes optional |

## 2. Data model (new platform app `tables`)

All models are TrackedModels (UUID keys, field history, sync). No model is created per user table:
records of all tables share one database table, so designing a table never needs a migration and the
sync schema fingerprint stays the same.

```
Table(TrackedModel)
  key            slug, unique, never changes (URLs, sync, print source "table:<key>")
  name, names    base name + {lang: name}, as masks/i18n.py
  icon, color, description, order, archived
  in_nav         shown in the sidebar section "Tabellen" (or the template's section)
  title_field    key of the field (or formula field) that names a record
  rights         {role: "none" | "read" | "edit"}; roles from role_choices(), module roles included
  template       {"id", "version"} when it came from a module template, else null

Field(TrackedModel)
  table          FK Table
  key            "f_<slug>", unique per table, never changes
  label, labels, help_text
  type           see §3
  options        choice values with colours and per-language labels (as FieldDefinition)
  config         per type: decimals, unit, min/max, link target and cardinality, formula, …
  required, unique, in_grid, order, archived

Record(RecycledModel)
  table          FK Table, never changes after creation
  values         {field_key: value}; dates ISO, numbers as canonical decimal strings, lists for multichoice
  title          cached display title (for links, search, sorting)
  search_text    cached lowercase text of the searchable fields

Link(TrackedModel)
  record         FK Record (the side holding the link field)
  field          field key
  target_kind    "record" | "pupil" | "staff" | "asset"
  target_id      UUID, indexed; deliberately not a database FK (see §6)
  order

Attachment(TrackedModel)
  record, field  as Link
  file           FileField under media/tables/<table>/<record>/; synced like person photos
  name, size, content_type
```

- **Link row ids are deterministic:** a single link uses `uuid5(record, field)`, so two computers
  changing it offline edit the same row and the usual last-writer-wins applies. A multiple link uses
  `uuid5(record, field, target)`, so adding the same target twice is the same row, and offline
  additions from two computers both survive.
- **No running numbers.** Computers create records offline, so a number like "Nr. 17" cannot be unique
  without the server. The record's short id serves instead. A server-assigned number is a later option.
- **Unique fields** are checked when saving. Two computers can still create the same value offline;
  sync then records a conflict for a person to resolve instead of refusing.

## 3. Field types

One registry (`core/fieldtypes.py`, extracted from `masks/services.coerce`): for each type, how to clean
an input, store it, show it, edit it in a form and in the grid, filter it, export it and print it.

| Type | Stored as | Notes |
|---|---|---|
| text, textarea | string | |
| number | decimal string | `decimals`, `unit` (e.g. €, kg), min/max. Person's number stays integer-only unless set |
| bool | true/false | |
| choice, multichoice | value / list | options with colours and translations |
| date, datetime | ISO string | datetime in the database's time zone |
| email, phone, url | string | validation as today |
| secret | encrypted string | context `record:<pk>:<key>`; reveal like person secrets; re-encoded in backups |
| link | `Link` rows | target own table or pupil/staff/asset; single or multiple; optional reverse name |
| file, image | `Attachment` rows | size limit; images get a thumbnail in lists and prints |
| formula | not stored | computed when read (§5); result type text, number, date or bool |

Changing a field's type is allowed only between compatible types (text ↔ textarea, choice → text,
number → text, …). Otherwise the dialog suggests a new field. Deleting a field archives it; its values
stay and come back if it is restored. Archived fields with values are emptied after a confirmation,
like the recycle bin.

## 4. Pages

- **Verwaltung → Tabellen** (capability `tables.design`): list of tables, "Neue Tabelle", "Vorlage
  hinzufügen …". A table's settings page has four tabs:
  - **Felder:** a sortable list and the field dialog: type, options, link target, formula editor.
  - **Datensatzseite:** opens the existing mask designer for the table.
  - **Rechte:** a role × none/read/edit matrix.
  - **Drucken:** the table's print templates.
- **Sidebar:** a section "Tabellen", or the template's own section name, with each table that has
  `in_nav`. Only tables the user may read appear.
- **List** (`/tabellen/<key>/`): the generalised grid. Columns come from the fields, with inline editing
  where the role may edit. It has filters, saved views (`SavedView.entity = "table:<key>"`), export,
  bulk print, bulk delete into the bin, and a "Papierkorb" link.
- **Record page** (`/tabellen/<key>/<id>/`): the table's published mask, edited field by field with
  htmx as on the person page. Formula fields refresh after each save.
- **New blocks for the mask designer:**
  - "Verknüpfte Datensätze": the records of another table linking here, as a small list with "Neu …".
  - "Anhänge".
  - "Verlauf".
  - On the person and device pages, a block lists the records of own tables linking to that person or
    device. It is added through `add_block`.
- **Global search:** finds records by title and searchable fields in readable tables.
- **Link picker:** a search box over the target's titles (persons by name and class, devices by number).
  A multiple link shows chips.

Data volume: grid.js loads all rows and filters in the browser. For pupils (some hundred) that is
fine. Tables are tested with 20,000 records. If a list is slower than 1.5 s, paging and filtering move
to the server for tables, without changing the grid's look.

## 5. Formulas

A small language of its own, parsed and evaluated in Python. There is no `eval`, and no access to
Python, files or the network. The same code runs on the server and in the computer's sidecar.

```
{Preis} * {Menge}
{Vorname} & " " & {Nachname}
if({Rückgabe} = null, days({Ausleihe}, today()), 0)
count({Ausleihen})                     links of a field, or reverse links
sum({Ausleihen}.{Gebühr})              a field of the linked records
```

- **Elements:** numbers, text in quotes, `true/false/null`, and fields in braces. Operators: `+ - * /`,
  `&` (joins text), `= != < <= > >=`, `and or not`, and parentheses.
- **Functions** (English names, the same in every language):
  - `if`, `isempty`, `coalesce`
  - `round`, `floor`, `ceil`, `abs`, `min`, `max`
  - `len`, `upper`, `lower`, `trim`, `concat`, `contains`
  - `today`, `now`, `year`, `month`, `day`, `days`, `age`, `format`
  - over links: `count`, `sum`, `avg`, `first`, `join`
- **Storage:** fields are stored by key and shown by name in the user's language, so renaming a field
  or switching the language never breaks a formula.
- **Checked when saved:** syntax, unknown fields, types (`date + text` is refused) and cycles between
  formula fields. The dialog previews the result for the first three records.
- **Limits:** length 2,000 characters, nesting depth 32, 10,000 steps per evaluation, 1,000 linked
  records per aggregate. Errors show as "#Fehler" with the reason in a tooltip, never a crash.
- **Ready-made computations:** "Anzahl verknüpfter Datensätze", "Summe eines Feldes verknüpfter
  Datensätze", "Tage zwischen zwei Daten", "Alter aus Geburtsdatum" and "Text zusammensetzen" are
  presets in the field dialog. Each fills in a formula that can be edited afterwards.
- **Computed when read, never stored:**
  - nothing to sync and no conflicts
  - a changed formula applies everywhere at once
  - lists evaluate per row with the links fetched once per table
  - sorting and filtering work on the computed values like any column
- Formula fields can be shown, sorted, filtered, exported and printed, but not imported or edited.

## 6. Rights, sync and security

- **Capabilities:**
  - `tables.design` (IT) for tables, fields, masks, rights and templates
  - the per-table rights for records
  - IT always has edit
  - `has_capability` gets a check `table_right(user, table)` used by every records view, export,
    print and import.
- **Links show only what the user may see.** If a user may read a table but not its link target (for
  example pupils without `person.view`, or a table with "none"), the link shows "nicht sichtbar"
  instead of a name. The person page lists linking records only from tables the user may read.
- **Sync read scope** (hook of the `tables` app):
  - always: the schema (Table, Field, masks of tables) of readable tables
  - records, links and attachments of readable tables
- **Why links are not foreign keys:** the scope closes the read set over foreign keys. A database
  foreign key from Link to Person would pull pupils onto a computer whose role may not see them.
  `target_id` as a plain UUID avoids that. A missing target shows "nicht sichtbar". Links to records
  removed for good are cleared by the nightly hook.
- **Sync write scope:**
  - records, links and attachments need edit on their table
  - schema models need `tables.design`
  - `Record.table` never changes (ignored key)
  - The server validates incoming values with the field type registry and refuses invalid ones, as
    a browser save would.
- **Changing rights evicts records** from computers that may no longer read them; the server already
  evicts records that leave the scope.
- **Schema changes while a computer is offline:** a value for a field archived meanwhile is kept but
  hidden. A value no longer valid for a changed field is kept and shown as a warning on the record.
- **Backups:** automatic. Restore and transfer (1.1) work unchanged, and the checks for "newer
  version" and modules apply.
- **Audit:** creating and changing tables, fields, rights and templates, bulk deletes and imports go
  to the audit log. Record values go to ChangeLog per key.

## 7. Import, export, printing

- **Import:**
  - `transfer/importer.py` gets a target protocol: `targets()`, `find(match)`, `clean(row)`,
    `apply(row)`, `undo()`.
  - Today's Person code becomes `PersonTarget` without behaviour changes.
  - `TableTarget` maps columns to fields.
  - Match key: any unique field, or "always new".
  - Link columns are matched by the target's title or by a chosen field, with a preview of what could
    not be matched.
  - Formula fields are not offered.
  - Preview, 24-hour undo and the claim on the session stay as they are.
- **Export:** list export for `table:<key>`, with the formula values as shown.
- **Printing:**
  - `PrintTemplate.source` becomes a free field (64 characters) checked against `available_sources()`.
  - The `tables` app lists one source per readable table.
  - Records come in the existing shape (`fields` as formatted text, `photo` from the first image field).
  - The designer's list element gets a "Verknüpfung" choice and prints the linked records with chosen
    columns, for example an invoice with items or a pupil's loans.
  - Sample records come from invented values per field type.
  - "Alle" prints every record of the current list filter.

## 8. Module templates

A template is a JSON file. Example: a library template with books, loans linking to pupils, a loan
slip and a list of overdue loans.

```json
{
  "id": "bibliothek", "version": 1, "title": "Bibliothek", "titles": {"en": "Library"},
  "section": {"title": "Bibliothek", "icon": "book"},
  "tables": [{"key": "buecher", "name": "Bücher", "fields": [...], "rights": {"sekretariat": "edit"}}],
  "masks": [...], "prints": [...], "views": [...],
  "demo": {"buecher": [{"f_titel": "…"}]}
}
```

- **Applying a template creates the same ids everywhere:** they are `uuid5` from template id and key,
  so a server and its computers end up with identical tables. Templates are applied on the server or
  in a database of its own; connected computers receive the result through sync.
- **Upgrades only add.** A newer version adds new tables, fields, masks and prints. It never deletes
  anything and never overwrites what the school has changed. Changed items are listed as "angepasst,
  nicht aktualisiert".
- **Data-only bundles:** a signed `.dfmodule` may carry only templates (`"apps": []`), so future basic
  modules need no Python and no platform release. The loader accepts them, and they show in the
  wizard and under "Vorlage hinzufügen …".
- **Schulverwaltung becomes optional:**
  - `REQUIRED` becomes `("platform",)`. The apps people and devices stay installed for their
    migrations but can be inactive.
  - The new-database wizard gets the card "Leer" (tables only) and the template cards.
  - Every platform place that assumes people (dashboard redirect, search, print sources, import,
    sync scope, checklist) gets a check for whether Schulverwaltung is active.
- The plan ships one template as a test and demo: the library above.

## 9. Steps (each with tests, committed separately, released together as 1.2.0)

1. **Field type registry:** extract `coerce`, display and editors from `masks/services.py` into
   `core/fieldtypes.py`, and add decimals, datetime, link, file and formula. Person behaves exactly as
   before (the whole suite must stay green). ChangeLog splits JSON dicts per key.
2. **`tables` app:** models, migrations, services (read and write values, title, search text,
   validation), the table settings pages (fields, rights), recycle bin, history, audit, sync hook,
   backup secrets.
3. **Lists and record pages:**
   - grid.js made entity-neutral; people lists unchanged
   - `Mask.table`, a default mask per new table, record page
   - inline editing, saved views, sidebar section, global search
4. **Links:** Link model, picker, reverse-link and attachment blocks, person and device block,
   visibility rules, nightly cleanup. Sync e2e covers a computer whose role may not see the target.
5. **Formulas:** parser, type checker, evaluator, presets, editor with preview, use in grid, record,
   export and print. Tests for the grammar, limits, cycles and error display.
6. **Import and export:** target protocol, PersonTarget (Person import unchanged, tests green),
   TableTarget, link matching, undo.
7. **Printing:** free source field, table sources, list element over links, sample records, bulk print
   from the list.
8. **Module templates:** format and schema check, apply and upgrade, data-only bundles, the library
   template, the wizard cards "Leer" and templates, Schulverwaltung optional.
9. **Onboarding and release:**
   - tour steps for tables
   - checklist item "Erste eigene Tabelle"
   - translations (en, zh_Hans, zh_Hant)
   - docs: a user guide for tables and formulas
   - release notes and screenshots

## 10. Migrations and release

- New app `tables` (0001). Also:
  - `masks.Mask.table` (nullable FK)
  - `prints.PrintTemplate.source` longer and without fixed choices
  - `transfer.ImportSession.entity` longer
  - `core.SavedView.entity` stays at 30 characters (`table:` plus a key of up to 20)
- The schema fingerprint changes: **servers before computers**, as always. Computers on 1.1 stop
  syncing with a 1.2 server until they update. They already show that message.
- Premium: daz, kindergarten and teaching are untouched. The premium suite must pass against every
  step.

## 11. Verification

- The full suite on PostgreSQL and SQLCipher, with and without premium; `makemigrations --check`.
- Sync e2e, extended with:
  - two computers editing the same field and the same multiple link offline
  - a role with read only (edits refused)
  - a role without access (receives nothing, links "nicht sichtbar")
  - rights taken away (records evicted)
- A backup of a database with tables, attachments and secret fields, restored and transferred.
- Performance: 20,000 records with 30 fields and 3 formula fields. List under 1.5 s, record page under
  300 ms, print of 500 records.
- Formula fuzzing: random input never raises and always stays within the limits.
- The client smoke test with a template database. A click-through in the browser pane and in the built
  app, in all three languages.

## 12. Open points

- Which further templates come after the library (inventory, events, bookings)?
- Should a later version get server-assigned running numbers?
- Server-side paging for lists, if the performance test asks for it.
- Whether roles other than IT may design tables (the capability allows granting it later).
