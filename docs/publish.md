# Publishing a read-only website

A publication turns chosen tables of a database into a static website that anyone can read: a start page,
each table as a list or gallery, a page for every record with its header (cover, backdrop, stars) and its
fields, links between published records, and a search over everything. Nobody can add or change anything
on the website itself. You keep working in DataFluss, on a computer or on the server, and **Neu
veröffentlichen** sends only what changed.

Publishing needs IT or a role with the switch **Veröffentlichen** (Roles → Verwaltung).

## Setting one up

**Verwaltung → Einstellungen → Veröffentlichen → Neue Veröffentlichung.** The page of a publication has
three parts:

- **Inhalt:** the tables and, for each, the fields to publish, list or gallery, and whether files (PDFs and
  other attachments) can be downloaded. Pictures of chosen picture fields always come along. The title of a
  record is always published.
- **Ziel:** where the website goes (see *Targets*).
- **Einstellungen:** name, short name (used in the address on a server), language, the role, and whether
  search engines may list the website.

**Vorschau erstellen** builds the website and **Vorschau öffnen** shows it inside DataFluss without
publishing anything. **Veröffentlichen …** first shows what will become public: every table with its
fields, the number of records, and the role. This confirmation comes again whenever the content or the
role changes; after that, **Neu veröffentlichen** publishes straight away.

Publishing runs in the background; the page shows its progress and the result. Every publish is in the
log (Protokoll).

## What is never published

The website shows only what is **chosen** and what the **role** of the publication may see:

- Only tables and fields ticked under *Inhalt*. New tables and new fields are never added by themselves.
- Only what the role may read. A table the role cannot read, or a field hidden for the role (field rules,
  see [Roles and rights](roles.md)), stays out, also later: hiding a field for the role removes it, and its
  pictures, from the website at the next publish.
- Never: secret fields, the change history, users, the bin, archived tables and fields.
- Never anything of Schulverwaltung: pupils, staff, parents, devices and logins, not even as the target of
  a link. A link to a record of a table that is not published shows nothing.
- A formula that reads a field that is not published shows nothing.
- The search index holds only published values, never the record's full search text.
- Pictures are made anew (WebP, at most 1600 pixels): their EXIF data, such as GPS positions, does not
  come along.

Before anything is written, a check compares every field in the website with what the publication may
show. If it finds anything else, the publish stops and nothing is sent.

## Targets

| Target | Where | Notes |
|---|---|---|
| ZIP-Datei | Download | The whole website, for a USB stick or to upload yourself. |
| Ordner | A folder on this computer (app only) | For example a folder another program syncs. |
| Dieser Server | `https://<server>/p/<short name>/` (server only) | Public, without login. **Vom Server nehmen** removes it. |
| WebDAV | A folder on a web space or a cloud (Nextcloud …) | Only `https://`. |
| FTP / FTPS | A folder on a web space | Encrypted (FTPS) unless switched off on purpose. |

Passwords of a target are stored encrypted with the database's key and are never shown again. **Gespeichertes
Ziel testen** checks that the target can be reached and written.

Each publish compares the new website with what it put at the target last time (a file
`datafluss-manifest.json` there keeps the list), sends what is new or changed, and then deletes what is no
longer part of the website. Files at the target that DataFluss did not put there are never touched.
Pictures are named after their content, so an unchanged picture is never sent again.

Deleting a publication takes the website off this server. On a folder, WebDAV or FTP it stays where it is
until you delete it there.

## The website

- Plain HTML with relative links: it works from a folder, under `/p/…` and on any web space.
- Light and dark follow the visitor's system; a button switches.
- The search (top right) looks through all published tables; the filter on a table page through that
  table. Lists sort by a click on a column.
- Tables made from the *Filme & Serien* template show TMDB's logo and notice, as TMDB's terms ask.
- Search engines may list the website unless that is switched off; then every page says `noindex` and
  `robots.txt` asks them to stay away.
- On a server, the website is sent with a strict Content-Security-Policy and no cookies.

## Notes for developers

- `publish/viewer.py`: `PublicationViewer` is the reader a publication renders as. `tables/services.right`
  and `core/fieldrules.rules` ask it directly (`table_right`, `field_rules`); it has no capabilities. Every
  function that shows records must get it: several treat `user=None` as a system task that sees
  everything.
- `publish/snapshot.py` reads the database into plain data (no model objects reach the templates) and
  checks it (`check`); `publish/render.py` writes the files from it, deterministically, with a picture cache
  in `published/.cache/<id>/`; `publish/targets.py` brings a target up to date (standard library only: the
  desktop app's sidecar has no boto3 or paramiko); `publish/service.py` runs it in a thread.
- Tests: `tests/test_publish_safety.py` (unique markers in every place that must stay private, searched in
  every file of the website) and `tests/test_publish.py`.
- Publications are not synced: each server or computer keeps its own.
- Later: publishing automatically after changes or on a schedule, SFTP, Netlify, S3, GitHub Pages, a
  password for `/p/…`, and publishing to datafluss.de (a target with a token and the same manifest).
