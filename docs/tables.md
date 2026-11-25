# Own tables

Own tables keep data DataFluss does not know out of the box, such as library books, room bookings,
events or an inventory. You design the tables, their fields, record pages and printouts yourself, and
they work like the built-in lists: filters, saved views, inline editing, search, printing, import and
export, sync with a server and backups.

Designing tables needs IT or a role with "Tabellen gestalten". Who may read or edit the records is set
for each table and role, and a role can also see single fields read-only or not at all (see
[Roles and rights](roles.md)). Chosen tables can also be published as a read-only website (see
[Publishing](publish.md)).

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
numbers with leading zeros stay text. A column named like an identifier (Nummer, Seriennummer, ISBN, …)
whose values all differ becomes unique, so a later import of the same list updates its rows. The title column is guessed too (a column named like "Name" or
"Bezeichnung", else the first one whose values differ). After checking, one click creates the table and
imports every row; the import can be taken back under Import & Export within 24 hours.

## Field types

| Type | Notes |
|---|---|
| Text, Text (mehrzeilig) | |
| Zahl | Decimal places (0–6), a unit such as € or kg, minimum and maximum |
| Bewertung | Stars: 3–10 (default 5), in half or whole steps. Set by click (left half of a star = half), by keyboard (arrows, 1–9, Home/End, Delete) or typed as "3,5"; clicking the same value again clears it. Counts as a number in formulas, sorting and filters; prints as ★★★½☆ |
| Ja/Nein | |
| Auswahl, Mehrfachauswahl | Options can be renamed and translated without changing stored records |
| Datum, Datum und Uhrzeit | |
| E-Mail, Telefon, Link | Checked on entry |
| Geheim (verschlüsselt) | Encrypted, shown only to roles with password rights, never in plain text in the history |
| Verknüpfung | Points to records of another table, or to pupils, staff or devices (see *Links*) |
| Datei, Bild | Uploads; an image field shows a preview and prints as the record's photo |
| Berechnung | A formula (see *Formulas*) |

Fields can be required or unique. A field can change to a compatible type (text ↔ multi-line text,
choice → text, number → text, …). Values are converted. Text, for instance from an imported file, can
also become a choice (its values become the options), a number or a date, when every value fits; the
values that do not are named. A number can become a rating and back. For anything else, create a new
field.
Archiving a field hides it and keeps its values.

## Sections in the sidebar

Under **Allgemein**, a table gets a **Bereich in der Seitenleiste**, e.g. "Bibliothek" or "Verein"
(existing ones are suggested). Tables with the same section appear together under that heading in the
sidebar, before "Drucken und Daten"; tables without one stay under "Tabellen". The overview of tables is
grouped the same way, and **Umbenennen** there renames a section for all its tables at once. The role
editor lists the tables by section too.

## Record pages

**Neuer Datensatz** opens the new record with the cursor in its first field. Every field is saved when it
is left. The bar above offers **Weiterer Datensatz** for entering several in a row, and **Verwerfen**,
which removes a record that was never filled in (one with values goes to the bin). Records can be put in
the bin from their page or from the list; the bin brings back one or all.

Without any design, a record page shows all fields. **Datensatzseite → Im Maskendesigner gestalten**
opens the same designer as for pupils: tabs, sections, widths, notes, conditions ("show if") and the
history block. Fields a page does not place still appear at the bottom under "Weitere Felder", so a new
field is never invisible.

### Look: gallery and header

**Tabelle gestalten → Allgemein → Darstellung** gives a table pictures:

- **Titelbild**: an image field. The list then also shows the records as a **gallery** (switch **Liste |
  Galerie**; search, filters, sorting and saved views work the same, and a saved view remembers the
  layout), and the record page gets a header with the picture, title, subtitles, stars (editable there)
  and a short text. **Form des Titelbilds**: Hochformat (poster, book), Quadratisch or Querformat (photo).
- **Hintergrundbild im Kopf**: another image field shown darkened behind the header; without one, the
  title picture is shown blurred.
- **Liste öffnet als**: list or gallery.
- **Untertitel** (up to three fields), **Markierung auf der Karte** (a choice such as a status, shown as
  a badge), **Bewertung** (a rating field) and **Text im Kopf** fill cards and header.

Without a title picture, lists and record pages look as before.

## Links

A link field points to records of another own table, or to pupils, staff or devices. It can allow one
target or several, and it can have a name for the other side, such as "Ausleihen" for loans pointing to a
book.

- On the record page, a search box offers the targets you may see; arrow keys and Enter pick one. Chips show
  what is linked. A name that is not there yet can be created in the target table right from the search
  ("„Pippi“ neu anlegen"), if you may edit that table.
- Records, pupils, staff and devices show a section **Verknüpft in eigenen Tabellen** with everything
  that links to them, under the name for the other side.
- A link to something you may not see shows **nicht sichtbar** instead of a name.

### The other side as a field (Gegenfeld)

When a new link points to another own table, **Dort auch als Feld zeigen** (on by default) adds a field
of type *Gegenseite einer Verknüpfung* to that table, named like the other side ("Inhaber von"). Example:
"Räume" has a link "Rauminhaber" to "Personen"; each person then has a field "Inhaber von" listing their
rooms. Both sides are the same link: adding a room on the person's page sets that room's Rauminhaber, and
removing it there clears it. Changing it from the other side needs the right to edit the table that holds
the link (and the link field must not be read-only or hidden for the role). The field can be placed in the
mask, shown in the list, printed and exported like any other; it cannot be imported, since the values
belong to the link.

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
field (preferred) or the title field, or every row becomes a new record. Values of a choice that are not
options yet are added by an import of someone who designs tables (the check names them); for others
they are errors. Link columns name their targets: records by
title, people by "First Last", "Last, First" or e-mail, devices by number, several separated by `;`.
The preview lists new, changed and unchanged rows and what could not be found. An import can be taken
back within 24 hours, unless its records were changed since.

**Excel** and **CSV** in a table's list export what the list shows, as it is shown.

## Printing

Every table is a print source: **Druckvorlagen → Neue Vorlage → Tabelle: …**. A new printout of a table
does not start empty: the title, the first image as photo, the logo, then every field as name and value
in two columns and long texts below; remove or rearrange what is not wanted. Fields print as shown on
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
one step. Applying a template again, or a newer version of it, only adds what is missing; nothing is
deleted, and what you changed is kept and named. When an update of DataFluss brings a newer version of a
template applied here, the start page and the list of tables say so, with what it brings, and
**Ergänzen** adds it (for those who may design tables).

DataFluss comes with four **database templates**, each a database of its own:

| Template | What it holds |
|---|---|
| **Filme & Serien** | Watched films and series and a watchlist, from TMDB (see below) |
| **Bibliothek** | Books, readers and loans, with loan slip, book card and overdue list |
| **Termine** | Events with place, kind and who is in charge, with a notice to print |
| **Inventar** | Rooms and the items in them, with value, condition, labels and room lists |

**Neue Datenbank** in the app offers, in this order: **Leer**, **Schulverwaltung** (with sample data if
wanted), then the database templates. A database template is never mixed with the school's tables or with
another template: **Tabellen → Vorlage hinzufügen …** offers one only in a database without Schulverwaltung
that has no other one yet (and an applied one again, to bring it up to date). In a school database the
button is gone. Modules can bring further templates that add tables to their own module.

The earlier school library (loans to pupils, `bibliothek`) is no longer offered; where it is applied, it
stays and can still be updated.

### Filme & Serien

Watched films and series and a watchlist, filled from [TMDB](https://www.themoviedb.org/). It works in
every database, with or without Schulverwaltung, and creates the section "Filme & Serien" with:

- **Filme & Serien**: title, original title, kind, status (Merkliste, Gesehen, Abgebrochen), rating, year,
  runtime, seasons and episodes, genres, direction or creators, cast, plot, tagline, poster, background,
  pictures, cast photos, own notes, TMDB id and link, and computed from the viewings: last watched, how
  often, where. The list opens as a poster gallery; saved views **Merkliste**, **Gesehen** and **Beste
  zuerst**.
- **Sichtungen** (not in the sidebar, shown on the title): date, **Wie gesehen** (cinema, TV, Blu-ray/DVD,
  Netflix, Disney+, … – more options under the field's settings), with whom, note. A title can be watched
  any number of times.
- **Darsteller** and **Regie**: a record per person with photo, the films and series they are in (from the
  other side of the link), how many titles there are, how many of them you have watched, when you last
  watched one and their average rating; saved view **Am häufigsten gesehen**. Taking a title over from
  TMDB fills them: its first ten actors, and the directors of a film or the creators of a series. A person
  already there (by TMDB id) gets linked, never a second time. In the cast strip of a title, a name leads
  to the person's record.

On a title's page, **Gesehen** (or **Nochmal gesehen**) adds a viewing for a day and a way and sets the
status to Gesehen: this is how a title leaves the watchlist.

#### TMDB

**Einstellungen → Tabellen → TMDB** explains in four steps how to get a free key from
[themoviedb.org/settings/api](https://www.themoviedb.org/settings/api). Both the **API key** (v3, 32
characters) and the **API read access token** (v4, starts with `eyJ`) work. **Schlüssel testen** asks TMDB
before saving; a key TMDB refuses is not saved. The key belongs to the database, is stored encrypted and
reaches connected computers with the sync. Users with **Tabellen gestalten** may set it.

- **Aus TMDB hinzufügen** (list of a table with TMDB): search films, series or both (arrow keys and
  Enter work), choose **Gesehen** (with how) or **Auf die Merkliste**. Titles already in the table are
  marked and open instead of being added twice. The server talks to TMDB, never the browser.
- What comes over: all the fields above in the language of the interface (English text when TMDB has
  none), up to 10 cast members (for series across all seasons), the poster (500 px), the background (1280
  px), up to 4 more pictures (780 px) and photos of the first 8 cast members. Pictures are stored as files
  of the record: they work offline and on connected computers. About 0.5–0.8 MB per title.
- **Mit TMDB aktualisieren** fetches everything again; rating, status, viewings and notes stay. A record
  typed in by hand gets **Bei TMDB suchen** instead, to match it.
- Fields set read-only for a role are not changed by an import of that user.

#### Taking over a collection from MyMovies

**Sammlung übernehmen** (list of Filme & Serien) takes over a MyMovies collection exported as XML. The
browser reads the file, which may be far larger than a request, and sends only title, original title,
year, IMDb number, rating and "added" date of each entry. Each title is found at TMDB by its IMDb number
(by name when there is none) and taken over like **Aus TMDB hinzufügen**; from the collection come:

- the rating: 1–10 in MyMovies become ½–5 stars;
- a viewing on the day the entry was added;
- a film in the collection more than once (two editions): one title with a viewing per entry;
- the seasons of a series (one IMDb number): one title, one viewing (the day of the last season) and the
  average rating;
- what TMDB does not know: a record with the collection's title, rating and viewing only, listed at the end.

A title already in the table keeps its rating and only gets missing viewings, so a second run adds nothing
twice. The import runs in short steps with a progress bar (about 20 minutes for 500 titles); the page must
stay open, **Anhalten** and **Weiter** stop and resume it, and earlier runs are listed. Pictures make about
0.5–0.8 MB per title.

TMDB's terms ask for their logo and the sentence "This application uses TMDB and the TMDB APIs but is not
endorsed, certified, or otherwise approved by TMDB."; both appear in the search, below lists and record
pages of TMDB tables and on the settings page.

The terms also forbid keeping TMDB data longer than six months. So every title notes the day of its last
fetch (**Mit TMDB abgeglichen am**, tab TMDB), and **after five months DataFluss fetches it again by
itself**, with its pictures, in the language of the last import or update. Like **Mit TMDB aktualisieren**
this keeps rating, status, viewings and notes, but replaces what came from TMDB, including changes made
there by hand. The people of a title come along: their name and photo are fetched again when the title is
and they were not fetched in the last 30 days; people no title brings any more are fetched on their own. It is part of the nightly work: on a server every hour (`nightly_backup --if-due` in
`deploy/start.sh`), on a computer with a database of its own once a day while it is open; a computer
connected to a server leaves it to the server. Up to 40 titles per round, the oldest first; a refused key or
no connection ends the round, and the next one goes on. A title TMDB no longer knows keeps its values and
is asked for again five months later. Each round with updates leaves an entry in the log.

Which fields TMDB fills is set in the template, under `look.tmdb` (see below), so another template can use
TMDB with its own fields.


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
- `"database": true` makes it a database template, offered as a new database of its own in the place
  `"order"` gives it; `"listed": false` retires a template (no longer offered, still updated where applied).
- Formulas are written with field keys (`{f_wert}`, `{f_raum}.{f_name}`, `{@geraete.f_raum}` for the
  records of another table linking here).
- In demo records, a link names its target record by title.
- A newer version raises `version`; it may add tables, fields and the rest, never remove them. Settings of
  a table's `look` that are new in it are added; those already there stay. `"changes": {"2": {"de": "…",
  "en": "…"}}` says what a version brings, for the notice.
- Table keys of a template should start with a short prefix of its own (`inv_raeume`), so they never meet
  a table someone made by hand. A formula may use formulas of other tables of the template, in any order.
- A table may set `"section"` (its heading in the sidebar), `"in_nav": false` and `"look"` (see *Look*):
  `{"cover": "f_poster", "backdrop": "f_hintergrund", "subtitle": ["f_jahr", "f_art"], "rating":
  "f_bewertung", "flag": "f_status", "text": "f_handlung", "layout": "gallery", "shape": "poster"}`.
  `look.tmdb` connects TMDB: `people` names the tables of people a title links to (`{"cast": {"table":
  "fs_darsteller", "link": "f_darsteller"}, "directors": {…}}`), each with `look.tmdb.person` (`name`,
  `photo`, `tmdb_id`, `tmdb_url`, `synced`); `fields` maps TMDB data (`title`, `original_title`, `kind`, `year`,
  `runtime`, `seasons`, `episodes`, `genres`, `director`, `creators`, `cast`, `overview`, `tagline`,
  `poster`, `backdrop`, `images`, `cast_photos`, `tmdb_id`, `tmdb_url`, and `synced`, a date field for the
  automatic update) to field keys, `kinds` names the
  options for film and series, `genres` maps TMDB genre ids to options, and `watch` names the status
  field, its values and the viewings table. `tables/library/filme.json` is the full example.

## Databases without Schulverwaltung

When creating a database in the app, the card **Leer** starts without pupils, staff and devices: own
tables only, and so does every database template. Its start page lists the tables, and printing, import and
links offer tables only. Instead of Sekretariat and Schulleitung there are the roles Team and Gast.
