<p align="center"><img src="docs/logo.svg" width="96" alt=""></p>
<h1 align="center">DataFluss</h1>
<p align="center">Your own database. On your computer, on your server, or both — always in sync.</p>
<p align="center">
  <a href="https://github.com/rana-gmbh/DataFluss/releases/latest">Download</a> ·
  <a href="#quick-start">Quick start</a> · <a href="#modules">Modules</a> · <a href="#run-a-server">Server</a> ·
  <a href="docs/BUILD.md">Build from source</a>
</p>

![DataFluss with the Schulverwaltung module](docs/screenshots/hero.png)

## What is DataFluss?

DataFluss keeps structured data — people, classes, devices, logins — in databases you own.

- **Works offline.** Every database lives on your Mac or PC, encrypted.
- **Syncs when you want.** Connect a database to a DataFluss server and changes flow both ways, also
  after days without a connection.
- **Starts from a module.** Begin with *Schulverwaltung* (school administration) and shape it with your
  own fields, forms and print templates.
- **Your own tables.** Design tables with links, computed fields, record pages and printouts, or start
  from a template such as [films & series](#example-films--series) or the library. No code needed.
- **Prints well.** Design ID cards, lists and letters, and print them as PDF.
- **Open source** under the AGPL-3.0.

| | |
|---|---|
| ![The start page of the Schulverwaltung](docs/screenshots/dashboard.png) | ![A pupil's record](docs/screenshots/record.png) |

## Example: Films & series

![The template Films & series: watched films and series as a poster gallery](docs/screenshots/films.webp)

A database template for the films and series you have watched and still want to watch:

1. **Create it.** In the app, choose **New database** → *Films & series*.
2. **Add your TMDB key once.** Under *Settings → Tables → TMDB*, paste a free key from
   [The Movie Database](https://www.themoviedb.org/settings/api). The page shows step by step how to get
   one and tests it before saving.
3. **Add a title.** Click **Add from TMDB**, type a few letters and pick the film or series. DataFluss
   fetches story, genres, year, runtime, cast, directors, poster and pictures by itself. The pictures are
   saved in your database, so they are there offline too.
4. **Note what you think.** Give up to five stars in half steps and add when and how you watched it
   (cinema, TV, Netflix, Disney+, …). Not seen yet? It goes on the watchlist.
5. **Bring your old list.** If you used MyMovies, export your collection as XML and click
   **Import collection**. Every title is found at TMDB and comes with all its pictures; your ratings and
   dates are kept, and nothing is added twice.
6. **Browse.** Switch between list and poster gallery, filter, and use the saved views for the
   watchlist, what you watched and your best rated. Actors and directors get their own pages with all
   their titles you know.

TMDB data is refreshed automatically every few months. This product uses the TMDB API but is not
endorsed or certified by TMDB.

## Download

| | |
|---|---|
| macOS (Apple silicon) | `DataFluss-<version>.dmg` of the [latest release](https://github.com/rana-gmbh/DataFluss/releases/latest) |
| Windows 10/11 (x64) | `DataFluss-<version>-setup.exe` of the [latest release](https://github.com/rana-gmbh/DataFluss/releases/latest) |
| Server | the Docker image `ghcr.io/rana-gmbh/datafluss` ([how](docs/server.md)) |

The apps update themselves. Server and apps always carry the same version number.

## Quick start

1. Install and open DataFluss. A short welcome explains the four ways in.
2. Click **Mit erfundenen Daten ausprobieren** to look around with invented people, or **Neue Datenbank**
   to start your own.
3. Pick a starting point, such as *Schulverwaltung*, name your database and create the first account.
   A short tour shows you where everything is; it is under **Hilfe** whenever you want it again.

![The start screen](docs/screenshots/start.png)

## Modules

A module brings ready-made sections, forms, print templates and sample data.

| Module | Contains | |
|---|---|---|
| **Schulverwaltung** | Overview, people (pupils, staff, parents, classes), IT (devices, repairs, logins), printing and data exchange, administration | Included |
| **Own tables** | Your own tables with links, formulas, record pages, printouts, import and export; templates such as *Films & series* and *Bibliothek* | Included |
| Further basic modules | Templates on own tables, as signed files without code | Included |
| Private modules | Built for one organisation, delivered as signed add-ons by its server | On request |

How modules plug in, and how a signed module reaches the computers of an organisation: [docs/modules.md](docs/modules.md).
Own tables, formulas and templates: [docs/tables.md](docs/tables.md). Publishing tables as a read-only website:
[docs/publish.md](docs/publish.md).

## Run a server

A server lets several people and computers work on the same data. It is the same application, in a
browser, plus sync for the apps. See [docs/server.md](docs/server.md) for Docker Compose and Coolify.

## How it fits together

```
[ DataFluss on a Mac ]  ⇄  [ DataFluss server ]  ⇄  [ DataFluss on a PC ]
   local, encrypted          web app and sync          local, encrypted
```

## Security

Databases are encrypted on disk (SQLCipher); keys stay in the macOS Keychain or the Windows Credential
Manager. Sign-in uses two-factor authentication. Updates and modules are signed. Please report
vulnerabilities as described in [SECURITY.md](SECURITY.md).

## Languages

German, English, Simplified and Traditional Chinese.

## Contributing

Bug reports and ideas are welcome as issues. Pull requests need a signed contributor agreement; see
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

AGPL-3.0, see [LICENSE](LICENSE). © Rana GmbH
