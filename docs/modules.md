# Modules

DataFluss is a platform: accounts and roles, forms ("masks") with your own fields, print templates,
import and export, backups and sync. A **module** brings the sections of one kind of database on top.

| | |
|---|---|
| **Basic modules** | Ship with DataFluss. The first is *Schulverwaltung*: people, classes, devices and logins. |
| **Private modules** | Built for one organisation, not part of this repository. They come as **signed bundles**. |

## How a module plugs in

- `schule/modules.py` lists the modules and their Django apps, without Django. `DATAFLUSS_MODULES`
  chooses the optional ones to load; without it every module present is loaded.
- `core/modules.py` collects what a module adds when Django starts: menu sections, rights and roles,
  search, start page, record blocks, print sources, its part of the sync scope, nightly work, demo data
  and steps of the guided tour. A module's `AppConfig.ready()` registers them; the platform never
  imports a module's code itself.
- Each database chooses the optional modules it uses (`SchoolSettings.modules`, set by the new-database
  wizard). What a module adds is left out where it is not used.

## Signed bundles

A bundle `<id>-<version>.dfmodule` is a zip with the module's apps, templates, static files,
translations and its own stylesheet, plus a manifest of every file's SHA-256, signed with Ed25519.

- **Loading.** `DATAFLUSS_MODULE_PATH` names the folders with bundles. Before Django starts, DataFluss
  unpacks new bundles, checks the signature against the keys in `schule/bundles.py` and every file
  against the manifest, and loads the bundles of exactly its own version. The desktop apps keep their
  bundles in `<app data>/modules`; "Modul installieren …" on the start screen adds one by hand.
- **Delivery.** A server that runs a module hands its bundle to the computers of its users
  (`POST /sync/v1/module`, with the user's own sign-in or a device token). A computer that connects to
  such a server fetches the bundle first; a connected computer keeps the bundle of the server's version,
  so it opens its database offline right after an update.
- **Building.** `scripts/build_module.py <module>` builds and signs a bundle from a module's apps.
- **Safety.** A database holding data of a module that is not loaded stays closed and unchanged, and a
  backup with such data is refused, instead of leaving data unseen.
