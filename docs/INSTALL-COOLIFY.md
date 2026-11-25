# Installing DataFluss on Coolify

This guide sets up the application on your existing Coolify server, including the update button.
Allow about 30 minutes. Do the steps in order, because some values are needed in later steps.

Tested on Coolify 4.5.3 on Ubuntu 24.04 in a virtual machine, through Coolify's API. The menu names
come from Coolify's documentation, so they can differ slightly on your screen.

> **Before you enter real data:** personal data and passwords should only go on this server once you
> are allowed to keep them there, and once you have tested backups (step 11). The demo data in step 10
> is invented and safe to use.

## What you will end up with

| Part | Role |
|---|---|
| PostgreSQL database in Coolify | All data |
| Application from the image `ghcr.io/rana-gmbh/datafluss` | DataFluss itself |
| Volume mounted at `/data` | Photos, files and backups |

Coolify's own proxy handles the domain and the HTTPS certificate, so nothing else on the server is
affected.

## 1. Create two secrets

On your Mac, run each command once and keep the results for step 6:

```bash
openssl rand -hex 32
```

```bash
openssl rand -base64 32
```

The first is `SECRET_KEY`, the second is `VAULT_KEY`.

**Store `VAULT_KEY` in a password manager now, and give a colleague a second copy.** All stored
passwords are encrypted with it. If it is lost, those passwords are gone for good, and no backup can
bring them back.

## 2. Create a project

In Coolify, open **Projects**, create a project named `DataFluss`, and open its `production`
environment.

## 3. Add the database

1. Select **+ New**, then **PostgreSQL**, and choose your server.
2. Under **Configuration**, set the image to `postgres:17-alpine`, then start the database.
3. Copy the **Postgres URL (internal)**. You need it in step 6.

Please use version 17: the application's backup tool matches it. With version 18 or newer, backups
fail and the update button refuses to run. Nothing breaks, but you could not update.

## 4. Nothing to prepare

The image is public, so Coolify needs no access to GitHub.

## 5. Add the application

1. In the `production` environment, select **+ New → Docker Image**.
2. Image: `ghcr.io/rana-gmbh/datafluss:latest`. Choose your server.
3. **Ports Exposes:** `8000`. Check this value carefully: with a wrong port, the deployment still
   succeeds, but the site shows Bad Gateway.
4. Continue to the application's settings, but **do not deploy yet**.
Then set these:

| Setting | Value |
|---|---|
| **Domains** | Your address with `https://`, for example `https://verwaltung.example.org`. If you keep the `sslip.io` address Coolify generates, change its `http://` to `https://` |

| **Advanced → Auto Deploy** | Off, so new versions only go live when you click update |
| **Persistent Storage → Add → Volume Mount** | Name `schulverwaltung-data`, Destination Path `/data` |

Leave the health check alone. It is built into the image, and Coolify uses it automatically.

Your domain's DNS must point to the server. Coolify then gets the HTTPS certificate by itself.

## 6. Set the environment variables

Open **Environment Variables** and add these:

| Name | Value | Available at Buildtime |
|---|---|---|
| `DEBUG` | `0` | |
| `SECRET_KEY` | from step 1 | **off** |
| `VAULT_KEY` | from step 1 | **off** |
| `DATABASE_URL` | Postgres URL from step 3 | **off** |
| `TIME_ZONE` | The school's time zone, for example `Asia/Shanghai` or `Europe/Berlin`. Without it the server uses `Europe/Berlin` | |
| `TEMPLATE_CATALOG_URL` | Optional. Where "Vorlage hinzufügen → Von datafluss.de" and the daily question for newer template versions ask (default `https://vorlagen.datafluss.de`); empty switches the template catalog off (docs/tables.md) | |

**Switch off "Available at Buildtime" for every secret, as marked.** It is on by default, and it
writes the secrets into the Docker image's history. Testing confirmed this, and that switching it off
removes them.

You do not need to set the domain or the application ID. Coolify provides both by itself.

## 7. Connect the update button

**Allow API access.** In Coolify, open **Settings → Configuration → Advanced** and switch on
**API Access**. Older versions call this **API Settings** under **Settings → Advanced**. If **Allowed IPs** is filled in, it must also allow the Docker network the application runs in.

**Create the token.** Open **Keys & Tokens → API Tokens** and create a token named `DataFluss`.
Tick **read** and **deploy** only, and copy it, as it is shown only once. The application can then
start deployments and see their status, but it cannot read your secrets or change anything else.


**Add these variables**, all with "Available at Buildtime" switched **off**:

| Name | Value |
|---|---|
| `COOLIFY_API_URL` | `http://coolify:8080`, Coolify's internal address. Use exactly this, not the address from your browser, because containers often cannot reach the server's public address |
| `COOLIFY_DASHBOARD_URL` | The address you open Coolify with in your browser, for example `https://coolify.example.org`. Only used for the link to the build log. Without it, the link is hidden |
| `COOLIFY_API_TOKEN` | The Coolify token |


**Private modules (optional).** An organisation with a private module (docs/modules.md) lets its
server fetch the module's signed file at start and hand it to the DataFluss computers that connect.
Add, with "Available at Buildtime" switched **off**:

| Name | Value |
|---|---|
| `DATAFLUSS_MODULE_PATH` | `/data/modules`, a folder on the volume, so the file survives updates |
| `DATAFLUSS_MODULE_FETCH` | The module's id |
| `DATAFLUSS_MODULE_REPOSITORY` | The GitHub repository whose releases carry the module |
| `GITHUB_TOKEN` | A token that may read that repository's contents |
## 8. Deploy

Select **Deploy** and follow the **Logs**. The first build takes a few minutes. It is finished when you
see `Rolling update completed`. Then open your domain, and the sign-in page should appear.

## 9. Create your account

In the application, open the **Terminal** tab, connect to the container, and run:

```bash
python manage.py create_admin yourname
```

Choose a password of at least 12 characters, then sign in. You will be asked to set up two-factor
sign-in, so have an authenticator app ready. Then upload your logo under **Einstellungen → Schule**.

For a demo without real data, IT users can switch two-factor sign-in off for everyone under
**Einstellungen → Benutzer**. Switch it back on before real data goes in.

## 10. Optional: load demo data

Only on a system without real data. Add the environment variable `ALLOW_DEMO_DATA` with the value `1`,
select **Redeploy**, then run this in a new terminal:

```bash
python manage.py seed_demo
```

Without the variable, the command refuses to run. It is your statement that this installation holds no
real people.

This also creates the demo accounts `it`, `sekretariat`, `leitung`, `daz` and `kiga`, gives 100 demo pupils a DaZ
record and fills the Kindergarten section for every child in a Kindergarten group. On a system that already holds demo data, the same command only adds what is missing. It changes
nothing once imported people are in the database. If what you imported is invented test data, add
`--imported-are-test-data`. Never use that option with real pupils, because they would get invented reports. To see the passwords:

```bash
cat .secrets/demo-logins.txt
```

Save them now, because the next update removes this file. The accounts themselves stay.

**Before real data goes in**, remove `ALLOW_DEMO_DATA`, select **Redeploy**, and lock the demo accounts.
This needs another active IT account, so create one with `create_admin` first (step 9):

```bash
python manage.py remove_demo_accounts
```

## 11. Set up backups

The application backs up the database before every update, but only into its own volume. You also need
regular backups that leave the server.

### The backup page

Under **Verwaltung → Sicherung**, IT users create a complete backup: the whole database, all stored
passwords, and all pictures and documents, in one encrypted file. Download the file and store it
somewhere else, because a copy on the same server does not protect you from a failed disk.

- **Password.** You choose a password for each backup. It is stored nowhere. Without it, the file
  cannot be opened by anyone, including you. Keep it apart from the file.
- **`VAULT_KEY` is not needed.** A backup carries its passwords under its own password, so it can be
  imported on any installation, whatever key that one uses.
- **Space.** Backups stay in `/data/backups` until you delete them on the page. Delete those you have
  downloaded.

To import a backup, upload it on the same page and select **Einspielen**. This **replaces all data** on
the installation, including the user accounts: afterwards, the accounts from the backup apply and
everyone is signed out. The application first saves the current state as a backup with the same
password. If an import fails, nothing has changed.

A backup from an older version can be imported, and the application brings it up to date. A backup from
a newer version is refused, so update the installation first.

### Automatic backups

Every night from 2:00, the application creates a complete backup with the password from
`BACKUP_PASSWORD`, reads the whole file back to prove that it is complete, and keeps the last seven.
The top of the backup page always shows how the last night went.

Add these environment variables with "Available at Buildtime" **off**, then select **Redeploy**:

| Name | Value |
|---|---|
| `BACKUP_PASSWORD` | At least 12 characters. Store it in your password manager, because the backups cannot be opened without it |

A backup on the same disk does not protect you from losing the server, so also set up a copy that leaves
it. Any S3-compatible storage works. Create a private bucket and an access key limited to that bucket,
then add these:

| Name | Value |
|---|---|
| `BACKUP_S3_BUCKET` | The bucket's name |
| `BACKUP_S3_ACCESS_KEY` | The access key |
| `BACKUP_S3_SECRET_KEY` | The secret key |
| `BACKUP_S3_ENDPOINT` | The provider's address, for example `https://s3.eu-central-003.backblazeb2.com`. Leave it out for Amazon S3 |
| `BACKUP_S3_REGION` | The region, if your provider asks for one |
| `BACKUP_S3_PREFIX` | Optional folder inside the bucket |

The file is already encrypted before it leaves the server. The storage keeps the last 30 nights.

To test it right away, run this in a new terminal, then check the backup page:

```bash
python manage.py nightly_backup
```

If a night fails, the application tries again every hour and the backup page says what went wrong. If only
the copy failed, it sends the same file again.

Coolify's own database backups are optional now. They do not include photos and files, and they are not
encrypted by the application, so only send them to storage that encrypts them.

**Test a restore once before you rely on it.** Import a backup into a second installation and look
through it.

## 12. Updating the application

New versions appear as releases of DataFluss. In the application:

1. Sign in with an IT account and open **Einstellungen → Aktualisierung**.
2. Select **Nach Updates suchen**. This also happens by itself about once an hour, and the start page
   tells IT users when a new version is available.
3. Check the list of changes and select **Jetzt aktualisieren**.

The database is backed up first, then Coolify builds the new version and only replaces the running one
once the new version is healthy. In testing this took about a minute, with no failed requests. Everyone
can keep working, and the page reloads by itself when it is done.

**If something goes wrong:**

| What happens | Result |
|---|---|
| The backup fails | Stops. Nothing has changed |
| Coolify cannot be reached | Stops. Nothing has changed |
| The new version does not build | The old version keeps running |
| The new version does not start | The old version keeps running |
| It does not start, and it already changed the database, within 15 minutes | The old version keeps running, and the application restores the backup by itself |
| The same, but noticed after more than 15 minutes | The old version keeps running, and the update page asks you whether to restore |
| Coolify gives no answer for an hour | The page tells you to check Coolify. If the database was changed, it offers the restore. Backups can be imported again, and if Coolify answers later, the page follows it |

When the database is restored, anything saved between the start of the update and the restore is lost.
That is why the application only does this by itself for 15 minutes. After that, the update page shows
the time it would go back to, and you decide. The application follows the update by itself, so you do not
need to keep a page open.

Only IT users can see this page or start an update, and every update is written to the audit log. The
page links to the full build log in Coolify.

## Troubleshooting

| Problem | Solution |
|---|---|
| Bad Gateway after a successful deployment | **Ports Exposes** must be `8000`. Correct it, select **Save**, then **Redeploy** |
| Update page says the connection is not set up | Check `COOLIFY_API_URL` and `COOLIFY_API_TOKEN`, then redeploy. Variables only take effect after a redeploy |
| "Coolify meldet Fehler 404" | Set `COOLIFY_API_URL` to `http://coolify:8080`, then **Save** and **Redeploy**. If it is already set, the token may belong to another Coolify team |
| "Coolify lehnt das Token ab" | The token needs **read** and **deploy**. Also check that API access is on |
| "Coolify ist nicht erreichbar" | Set `COOLIFY_API_URL` to `http://coolify:8080`, then **Save** and **Redeploy**. If Coolify runs on a different server, use an address the container can reach, and check **Allowed IPs** |
| "Coolify hat unerwartet geantwortet" | Check under **Deployments** in Coolify whether the update started anyway. If it did, let it finish |
| Log at start: `Module: … nicht geladen` | The download failed or the file's signature did not hold. Check `GITHUB_TOKEN`, then redeploy |
| "Die Datenbank konnte nicht gesichert werden" | The database must be version 17 or older, and the `/data` volume must exist |
| Backup page says `BACKUP_PASSWORD` is missing | Add it as in step 11, then **Redeploy** |
| Deployment stops at "/data ist kein Volume" | Add the volume as in step 5. Without it, photos and backups would vanish with the next update |
| Deployment stops at "VAULT_KEY passt nicht" | The key does not belong to this database. Enter the value from your password manager |
| "seed_demo" says demo data is not allowed | Set `ALLOW_DEMO_DATA=1` and redeploy, but only on a system without real people |
| Deployment stops at "Tresorschlüssel nicht lesbar" | `VAULT_KEY` is missing or wrong. Enter the value from your password manager |
| "Das Passwort stimmt nicht, oder die Datei ist beschädigt" | Check the password. If it is right, download the backup again from where you stored it |
| Sign-in page shows a CSRF error | Check that the domain in Coolify starts with `https://`, then redeploy |
| Locked out of two-factor sign-in | Another IT user can reset it under **Einstellungen → Benutzer**. If you are the only IT user, create a second account with `create_admin` (step 9) and reset it from there |

**To restore a backup from before an update or a school-year rollover by hand**, open the application's
**Terminal** and run the following, with the file name from `ls -t /data/backups`. These files are
encrypted with `VAULT_KEY`, so they only open with the key that was active when they were made. All data
saved since then is lost. The command then brings the database up to the running version.

```bash
python manage.py restore_dump FILE.sql.gz.enc --confirm
```

**Server in mainland China:** Docker Hub, GitHub and PyPI may be slow or blocked, and both builds and
updates depend on them. If builds hang while downloading, add the variable
`PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/` with "Available at Buildtime" **on**, and set a
registry mirror for Docker on the server.

## Microsoft 365 sign-in (optional)

Username and password work without this. To add Microsoft sign-in, register an app in Entra ID with the
redirect address `https://<your address>/oidc/callback/`, then add `OIDC_CLIENT_ID`, `OIDC_TENANT` and
`OIDC_CLIENT_SECRET` as variables, with "Available at Buildtime" off, and redeploy. `OIDC_TENANT` must be
your tenant ID, the long value from the app's overview page. The application refuses to start with
`common` or a domain name, because people from other organizations could then sign in. Accounts must already
exist in the application. The first sign-in finds the account by e-mail address, and after that the
Microsoft account ID counts.

## Moving to another server

Set up steps 2 to 8 on the new server with the **same** `VAULT_KEY`, then restore a database backup and
copy the `/data` volume. Without the original `VAULT_KEY`, stored passwords cannot be read.
