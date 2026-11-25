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

## Publishing by itself

Under **Automatisch veröffentlichen** a publication can publish on its own:

- **Nach Änderungen:** once something in its tables changed and nothing changed for 5 minutes, 15 minutes or
  an hour. Changes made on a computer connected to the server count as soon as they are synced.
- **Täglich:** every day at a set time.

It only does so after it was published once by hand, never to a ZIP, and never while it needs a
confirmation: when the content or the role changed, or when the role came to see more than was confirmed
(a chosen field no longer hidden for it, a chosen table it may now read). Then the page says **Wartet auf
Bestätigung**, and **Veröffentlichen …** shows the list again with what is new marked. Fewer fields, and new
or changed records, never ask. Even a publish that was already running when the role changed shows only
what was confirmed.

If an automatic publish fails (a web space that is down, a wrong password), it is tried again after half an
hour at the earliest; the page shows the error.

On a server, `deploy/start.sh` runs `manage.py publish_auto --loop`, which looks every minute. Elsewhere,
run `manage.py publish_auto` from cron. In the desktop app it runs while the database is open and catches up
when it is opened again.

## From a computer connected to a server

On a computer connected to a server, **Veröffentlichen** also lists the server's publications under **Auf
dem Server**: their state, address and automatic setting. **Neu veröffentlichen** sends this computer's
changes to the server first and then has the server publish, with the server's data. This needs the switch
**Veröffentlichen** on the server. Setting up and confirming a server's publication happens on the server
(**Auf dem Server bestätigen** opens its page).

Publications made on the computer itself publish the computer's own copy, which holds only what its user's
role may see.

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
| Dieser Server | `https://<server>/p/<short name>/` (server only) | Without a DataFluss login; optionally with a password (see below). **Vom Server nehmen** removes it. |
| DataFluss-Hosting | `databases.datafluss.de/<name>/`, or `/p/<name>/` on another DataFluss server | Address and token of a site made there (see *Hosting*). |
| WebDAV | A folder on a web space or a cloud (Nextcloud …) | Only `https://`. |
| FTP / FTPS | A folder on a web space | Encrypted (FTPS) unless switched off on purpose. |
| SFTP | A folder on a server reached by SSH | Password or private SSH key (without passphrase). The server's key is trusted on first use and then held to. |
| S3-kompatibel | A bucket of AWS S3, Hetzner Object Storage, Cloudflare R2, MinIO … | Endpoint, region, bucket, optional folder, access key and secret. Serving the bucket as a website is set up at the provider. |
| Netlify | A Netlify site | Site ID and a personal access token. Each publish is a deploy; Netlify asks only for files it does not have. |
| GitHub Pages | A branch (default `gh-pages`) and optional folder of a repository | A token with write access to the repository's contents. One commit per publish, with `.nojekyll`. |

Passwords of a target are stored encrypted with the database's key and are never shown again. **Gespeichertes
Ziel testen** checks that the target can be reached and written.

Each publish compares the new website with what it put at the target last time (a file
`datafluss-manifest.json` there keeps the list), sends what is new or changed, and then deletes what is no
longer part of the website. Files at the target that DataFluss did not put there are never touched.
Pictures are named after their content, so an unchanged picture is never sent again.

Deleting a publication takes the website off this server. Anywhere else it stays where it is until you delete
it there.

**SFTP host key.** The first connection (best: **Gespeichertes Ziel testen**) records the fingerprint of the
server's key and shows it. From then on a different key stops every connection, as that is what someone in
between would look like. If the server was set up anew, save the target again (same server and port keep the key;
another server or port starts afresh) and test it.

### Password for a site on this server

Under **Ziel → Dieser Server**, **Mit einem Passwort schützen** asks visitors for a password before anything of the
website (pages, pictures, files) is sent. Whoever enters it gets a cookie for 30 days that only this website's
address receives. A new password signs everyone out. After ten wrong tries an address waits a quarter of an hour.
A protected website is never listed by search engines.

### Hosting

A DataFluss server can host websites that other computers publish to it: **Veröffentlichen → Websites von anderen
Geräten → Anlegen** gives an address under `/p/…` and a token, shown once. On the other computer the target
**DataFluss-Hosting** takes the server's address and that token. The same works with `https://databases.datafluss.de`
for accounts of datafluss.de that a reviewer unlocked (docs/website.md, *Websites*).

What arrives this way is not made by this server, so it is served apart from it: every page runs in a sandbox
(Content-Security-Policy `sandbox`), its scripts cannot use the server's sign-ins, files that are not part of a
website download instead of opening, and each site has a quota (500 MB on a DataFluss server).

The protocol (`publish/hosting.py`, used by both): `Authorization: Bearer <token>`; `GET /api/hosting/v1/site`
(name, address, quota, used); `GET|PUT|DELETE /api/hosting/v1/files/<path>`.

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
- `publish/auto.py`: a publication's last change is the newest `sync.Journal` row of its tables' records,
  or of any table, field, link, file, role or record page (`marker`); `due` and `tick` decide and publish.
  `Publication.published_choice` holds the confirmed choice, role and resulting fields; `service.run`
  caps a publish to those fields and shrinks them when the role sees less.
- `publish/api.py` (`sync/v1/publish/list|run`, device token) and `publish/remote.py` serve a connected
  computer. Both names are in `core/middleware.py PUBLIC_NAMES`.
- `publish/cloud.py` (S3 with its own SigV4 signature, Netlify, GitHub, hosting), `publish/sftp.py` (paramiko),
  `publish/guard.py` (the password), `publish/hosting.py` + `publish/hosting_api.py` (hosting). Targets that take a
  whole state at once have `deploy(site, changed, gone, manifest)`; `targets.send` calls it instead of its loop.
- The template site's part of hosting is `web/seiten/` (docs/website.md).
