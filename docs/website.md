# The template site (templates.datafluss.de)

A website where people find, download, share and rate DataFluss database templates. Every upload and every
review text is checked by a reviewer before it appears. The site is its own Django project in `web/`, with its
own database and image; it shares only the template check (`tables/template_check.py`) with DataFluss.

| | |
|---|---|
| **Visitors** | browse, search and filter by category, look at a template (tables and fields, pictures, versions, reviews), download it. No account, no cookie. |
| **Signed in** | share templates and new versions, rate (½ to 5 stars) and review others' templates, report a template or review. |
| **Reviewers** | a queue of submitted versions, review texts and reports; approve or reject (with a reason the uploader gets by mail). |

## Templates people share

A shared template is a **database template** (`"database": true`) that stands on its own: no modules
(`requires: []`), links only between its own tables, never to pupils, staff or devices. People make one in
DataFluss under **Einstellungen → Tabellen → Als Vorlage exportieren** (see [Own tables](tables.md)).

Templates contain no code, but their texts reach pages, styles and printouts of every database that applies
them. So nothing someone else wrote is trusted:

- **`check_untrusted`** (`tables/template_check.py`) runs on upload, again on approval, and (from DataFluss 1.4)
  before a database applies a file from the catalog. It knows every key at every level with its type (template,
  table, field per type, option, `look` and `look.tmdb`, record page and print trees, saved views, sample
  records) and refuses everything else; every reference must point into the template; ids, colours, widths,
  print values and sizes are held to what they may be. Limits: 1 MB, nesting depth 20, 30 tables, 150 fields per
  table, 200 options, 50 record pages, printouts and views each, 200 sample records per table, 2,000 nodes per
  tree. Whatever slips through raises nothing: the result is "invalid template".
- **Ids** of the platform's own templates are reserved, and an id belongs to whoever shared it first, so a
  shared template can never "update" someone else's tables.
- **Pictures** are decoded and written anew as WebP (no metadata; at most 4 per version, 5 MB each, 1600 px wide).
- **For the reviewer**, `notes()` points out what a person should look at: links in texts, sample records (real
  personal data?), fields that look personal, rights to edit.
- DataFluss itself escapes these values where they are shown (record page tabs, list column titles, printouts),
  so a template that got past the check still could not run script.

The catalog hands out the approved file as it was checked, with `"database": true`, `"requires": []` and where it
comes from: `"catalog": {"source": "datafluss.de", "author", "license": "CC-BY-4.0", "url"}` (DataFluss ignores keys
it does not know). The file is written once when approved; its SHA-256 is part of the index.

### Versions

A new version keeps the id and has a higher `version`. The shown version stays until the new one is approved;
uploading again while one is waiting replaces the waiting one. Where a template is applied, a newer version only
adds (tables/blueprints.py): what the earlier one brought stays there, even if the new file no longer has it.

## Accounts and sign-in

- **By link, without a password.** An address gets a link valid for 15 minutes and only once (it is signed and
  holds the account's last sign-in, `konten/tokens.py`). The link page shows a button; only the click signs in,
  so mail programs that open links to check them do not spend it. An account is made when its first link is used,
  so nobody can create accounts for addresses they do not read. The answer is the same for known and unknown
  addresses.
- A **name** is needed before sharing or rating: it is shown with templates and reviews (attribution under
  CC BY 4.0). The address is never shown.
- **Reviewers** (`is_staff`) also confirm a code from an authenticator app once per session; they set it up on
  first use (QR code). Make the first reviewer with `python web/manage.py createsuperuser` (address and name; no
  password is used).
- **Deleting an account** (Konto) removes its reviews and the templates that were never published; published
  templates stay under "gelöschtes Konto", since they were shared under CC BY 4.0 (they can be withdrawn first).

## Ratings and reviews

One review per person and template: ½ to 5 stars in half steps, and an optional text. Stars count at once; a new
or changed text waits for a reviewer. Nobody rates their own template or an official one. The average shows as
stars and as a number ("4,5 (12)").

## Abuse protection

No outside service is involved. Rate limits per address, account and IP (5 sign-in mails per address and 20 per
IP an hour, 10 uploads, 30 reviews and 20 reports per account a day; `konten/limits.py`, keys stored as keyed
hashes for a day), a hidden trap field and a minimum time of 3 s on every form people fill in, sizes as above,
the review before anything appears, and **Melden** for templates and review texts. Should spam get through
anyway, a self-hosted proof-of-work captcha (ALTCHA) would be the next step.

## Privacy and legal

- No tracking, no analytics, no fonts or scripts from other servers; a strict content policy (`script-src 'self'`,
  no inline script or style). The only cookie is the session after signing in (and the CSRF cookie of forms).
- Pages **Impressum**, **Datenschutz** and **Nutzungsbedingungen** (`web/templates/recht/`) are **drafts** with
  placeholders in [brackets]; they must be completed and checked legally before the site goes public.
- Shared templates are licensed **CC BY 4.0**; uploaders confirm that and that the file holds no real personal data.

## Interface for DataFluss

Stable, under `/api/v1/`; fields are only ever added.

- `GET /api/v1/vorlagen.json`: `{"format": 1, "templates": [{"id", "version", "title", "titles", "description",
  "descriptions", "category", "official", "author", "license", "rating", "ratings", "downloads", "has_demo",
  "datafluss", "tables", "sha256", "size", "updated", "url", "page", "changes", "picture"}]}`. `datafluss` is the
  version of DataFluss the template was exported from (empty for older files); `changes` is the template's own
  `{version: {language: text}}`, shown in DataFluss's update notice; `picture` the first screenshot or empty.
- `GET /api/v1/vorlagen/<id>/<version>.json`: the file (counted as a download). Its SHA-256 matches the index.

DataFluss (from 1.4) uses it for "Vorlage hinzufügen → Von datafluss.de", the app's "Neue Datenbank" and the daily
question for newer versions (`tables/catalog.py`, docs/tables.md). It accepts only the catalog's own origin over
https, checks size and SHA-256 against the index and runs `check_untrusted` itself before applying.

Who asks, and when: a DataFluss server or a computer with its own database when someone opens "Vorlage hinzufügen"
or picks a catalog template, and once a day while a catalog template is applied there (can be switched off); the
app when its "Neue Datenbank" wizard opens. The site logs nothing of this beyond the server's access log.

## Websites

People publish read-only websites from DataFluss to **`databases.datafluss.de/<name>/`** (docs/publish.md, target
*DataFluss-Hosting*). It lives in `web/seiten/`:

- An account asks for websites under **Websites** with a short reason; a reviewer unlocks it (or refuses, with a
  reason sent by mail) under **Prüfen → Websites prüfen**. Unlocked accounts make up to `SITES_PER_ACCOUNT` sites of
  `SITES_QUOTA_MB` each; each site has a token, shown once, that DataFluss uses to publish.
- Files are served only on the sites' own host (`SITES_URL`), never on the template site's: `SitesHostMiddleware`
  answers every request to that host before sessions or CSRF, so no cookie of the accounts is read or set there.
  Each page also runs in a sandbox (`publish/hosting.py SANDBOX_CSP`), and files that are not part of a website
  download instead of opening.
- Anyone can report a site under **Websites → Eine Website melden**; reviewers switch a site off or block the
  account (which switches all its sites off and refuses its tokens).
- The hosting protocol is DataFluss's own (`publish/hosting.py`, copied into the image like the template check).
- `check --deploy` refuses a `SITES_URL` that is not https, that is the template site's own host or below it, and
  any `SESSION_COOKIE_DOMAIN`/`CSRF_COOKIE_DOMAIN` (web.E101–E103): the accounts' cookies stay host-only.

**Before it goes public:** the terms of use and the imprint need a section on websites people publish
(responsibility for content, removal on notice); that is a legal text for you to write.

## Running it

Locally (SQLite in `web/data/`, mails in the console):

```bash
DEBUG=1 .venv/bin/python web/manage.py migrate
```

```bash
DEBUG=1 .venv/bin/python web/manage.py import_library
```

```bash
DEBUG=1 .venv/bin/python web/manage.py runserver 127.0.0.1:8140
```

`import_library` publishes DataFluss's own database templates as official ones. Tests:
`.venv/bin/python web/manage.py test webtests` (CI job `web`).

### Start on templates.datafluss.de (Coolify)

The site runs at `https://templates.datafluss.de` as its own Coolify application on the same server as the demo, with
its own PostgreSQL. Everything below happens in your accounts; nothing of it is set up from this repository.

1. **DNS.** An `A` record `templates.datafluss.de` → the IP address of the Coolify server (or a `CNAME` to its host
   name), and the same for `databases.datafluss.de` (websites people publish). `datafluss.de` itself stays free for a
   product page.
2. **Mailbox.** A mailbox such as `templates@datafluss.de` with SMTP access at your mail provider. For datafluss.de,
   SPF (the provider's `include:`), DKIM (the provider's key) and a DMARC record (`v=DMARC1; p=quarantine; …`), so
   sign-in links do not land in spam. A data processing agreement with the provider (Art. 28 GDPR).
3. **Coolify, PostgreSQL.** New resource PostgreSQL 17, scheduled backups on (daily, keep 14).
4. **Coolify, application.** New resource → public/private repository `rana-gmbh/DataFluss`, branch `main`, build
   pack **Dockerfile**, base directory `/`, Dockerfile location `web/Dockerfile`, port `8000`, health check path
   `/healthz/`, domains `https://templates.datafluss.de,https://databases.datafluss.de`. Persistent storage: a volume mounted on `/data` (pictures in
   `/data/media`; back it up with the server's backups).
5. **Environment** (all runtime only: switch off "Available at Buildtime" for each):

   | Variable | Value |
   |---|---|
   | `SECRET_KEY` | a new random value of at least 50 characters (`python3 -c "import secrets; print(secrets.token_urlsafe(60))"`) |
   | `SITE_URL` | `https://templates.datafluss.de` |
   | `DATABASE_URL` | `postgres://user:password@host:5432/name`, the internal URL Coolify shows for the PostgreSQL resource |
   | `TRUST_PROXY` | `1` |
   | `EMAIL_HOST`, `EMAIL_PORT` | the provider's SMTP server, `587` (STARTTLS) or `465` with `EMAIL_USE_SSL=1` |
   | `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | the mailbox's login |
   | `DEFAULT_FROM_EMAIL` | `DataFluss Templates <templates@datafluss.de>` |
   | `REVIEW_NOTIFY` | your own address(es), comma-separated: new uploads, review texts, reports |
   | `SITES_URL` | `https://databases.datafluss.de` (websites people publish; see *Websites*) |
   | `SITES_QUOTA_MB`, `SITES_PER_ACCOUNT` | optional, `200` and `3` |

6. **Deploy**, then in the application's terminal (the container starts in `/app`):

   ```bash
   python web/manage.py check --deploy
   ```

   No errors expected; `web.W…` warnings name settings that are missing.

   ```bash
   python web/manage.py sendtestemail you@example.org
   ```

   ```bash
   python web/manage.py createsuperuser
   ```

   (your address and name; no password is used: sign in by link, then set up the second factor)

   ```bash
   python web/manage.py import_library
   ```

7. **First round:** sign in, set up the second factor, upload a template with a picture, approve it, rate it with
   half stars, download it; `https://templates.datafluss.de/api/v1/vorlagen.json` lists it with its SHA-256.
8. **Legal pages** completed and checked; only then share the address.

**Updates:** Coolify builds the image again on every push to `main` (the site's tests run in CI job `web`); the
container migrates its database when it starts (`web/start.sh`). **Backups:** the PostgreSQL resource's scheduled
backups and the `/data` volume.

`check --deploy` silences only HSTS for subdomains and the preload list (`security.W005`, `security.W021`): those
are decisions for the whole domain datafluss.de. HTTP is redirected to HTTPS by the site itself as well, except
`/healthz/`, which the container's health check asks over plain http.
