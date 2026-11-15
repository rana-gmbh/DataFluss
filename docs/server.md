# Running a DataFluss server

The server is the same application as the desktop apps, in a browser, plus sync for the apps. It runs
as the Docker image `ghcr.io/rana-gmbh/datafluss` with a PostgreSQL 17 database and a volume for photos,
files and backups.

## With Coolify

Step by step, including HTTPS, the update button and backups: [INSTALL-COOLIFY.md](INSTALL-COOLIFY.md).

## With Docker Compose

```yaml
services:
  db:
    image: postgres:17-alpine
    environment:
      POSTGRES_USER: datafluss
      POSTGRES_PASSWORD: change-me
      POSTGRES_DB: datafluss
    volumes:
      - db:/var/lib/postgresql/data
  app:
    image: ghcr.io/rana-gmbh/datafluss:latest
    depends_on: [db]
    ports:
      - "8000:8000"   # put a reverse proxy with HTTPS in front of it
    environment:
      DEBUG: "0"
      SECRET_KEY: ${SECRET_KEY}       # openssl rand -hex 32
      VAULT_KEY: ${VAULT_KEY}         # openssl rand -base64 32, keep a copy: stored passwords depend on it
      DATABASE_URL: postgres://datafluss:change-me@db:5432/datafluss
      ALLOWED_HOSTS: datafluss.example.org
      CSRF_TRUSTED_ORIGINS: https://datafluss.example.org
      TIME_ZONE: Europe/Berlin
    volumes:
      - data:/data
volumes:
  db:
  data:
```

Then create the first account:

```bash
docker compose exec app python manage.py create_admin yourname
```

## Moving a database from the app to the server

A database that started in the desktop app can become the server's data, with all its accounts,
pictures and documents (DataFluss 1.1 on both sides). Create the first account on the new server as
above and sign in once in the browser to set up its second factor. Then, in the app, open the database
and go to **Verwaltung → Sicherung → Auf einen Server übertragen**: the server's address, that account
with its code, and a password for the backup. The app packs the database, uploads it and the server
restores it. Afterwards the server has the database's accounts; connect the app anew with
**Mit Server verbinden** and work with the server from then on.

If the server holds data already, the app names it and asks before replacing it; the server keeps a
backup of its previous state under **Sicherung**, with the same password.

To update: `docker compose pull && docker compose up -d`. The database migrates when the new version
starts, after a backup of it in `/data/backups`. Update the server before the desktop apps: computers
only sync with a server on the same version.

The other settings (nightly backups with an off-site copy, Microsoft sign-in, private modules) are the
same as in the Coolify guide.
