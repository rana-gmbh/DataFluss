# Contributing to DataFluss

Thank you for helping. Bug reports, ideas and pull requests are welcome.

## Issues

Describe what you did, what you expected and what happened instead, with the DataFluss version
(Einstellungen shows it) and your operating system. Please never attach real personal data: use the
demo database (**Mit erfundenen Daten ausprobieren**) to show a problem.

Security problems do not belong in issues; see [SECURITY.md](SECURITY.md).

## Pull requests

1. **Contributor agreement.** Before your first pull request can be merged, you sign the
   [Contributor Licence Agreement](CLA.md). A bot asks you to do so in the pull request; it takes one
   comment. DataFluss is open source under the AGPL-3.0, and Rana GmbH also offers private modules on top
   of it. The agreement lets Rana GmbH do both with code you contribute, while you keep your copyright.
2. **Tests.** Run the tests before you open the pull request (see [docs/BUILD.md](docs/BUILD.md)) and add
   tests for what you change.
3. **Small and focused.** One change per pull request, with a short description of the why.
4. **Language.** Code, comments and commit messages in English. The interface is German first: new texts
   in German, marked for translation; translations into English and Chinese are welcome.
5. **Migrations.** A pull request that changes models brings its migration. Every migration needs a new
   DataFluss version for server and apps together, so mention it in the description.

## Code of conduct

Be kind and assume good intent. Harassment of any kind is not tolerated.
