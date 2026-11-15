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

To update: `docker compose pull && docker compose up -d`. The database migrates when the new version
starts, after a backup of it in `/data/backups`. Update the server before the desktop apps: computers
only sync with a server on the same version.

The other settings (nightly backups with an off-site copy, Microsoft sign-in, private modules) are the
same as in the Coolify guide.
